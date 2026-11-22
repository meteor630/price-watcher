import Foundation

enum APIError: LocalizedError {
    case invalidServer, insecureServer, invalidResponse, sessionExpired, keychain
    case http(Int, String?)
    case conflict(ProductDetail?)

    var errorDescription: String? {
        switch self {
        case .invalidServer: "Введите полный адрес сервера, например http://192.168.1.20:8000."
        case .insecureServer: "Для подключения вне домашней сети нужен адрес HTTPS."
        case .invalidResponse: "Сервер вернул неожиданный ответ. Проверьте адрес и версию PriceWatcher."
        case .sessionExpired: "Сессия истекла. Получите новый код на сайте."
        case .keychain: "Не удалось открыть защищённое хранилище iPhone."
        case .conflict: "Товар уже изменился на сервере. Проверьте обновлённые значения."
        case .http(429, _): "Слишком много попыток. Попробуйте позже."
        case .http(_, "PAIRING_CODE_INVALID"): "Код истёк, уже использован или введён с ошибкой."
        case .http(_, "PAIRING_CODE_EXPIRED"): "Срок действия кода истёк. Получите новый код."
        case .http(_, "WORKER_OFFLINE"): "Парсер недоступен. Запустите worker на компьютере."
        case .http(_, "RUN_IN_PROGRESS"): "Проверка уже идёт."
        case .http(403, _): "У вашей учётной записи нет доступа к этому действию."
        case .http(404, _): "Запись не найдена. Обновите данные."
        case .http(409, _): "Данные изменились на сервере. Обновите экран."
        case .http(422, _): "Сервер отклонил введённые данные."
        case .http(500...599, _): "Сервис временно недоступен. Попробуйте ещё раз."
        case let .http(status, _): "Сервер отклонил запрос (HTTP \(status))."
        }
    }
}

private struct ServerErrorResponse: Decodable {
    let error: Body

    struct Body: Decodable {
        let code: String
        let details: Details?
    }

    struct Details: Decodable {
        let current: ProductDetail?
    }
}

private final class RejectRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

actor APIClient {
    let server: ServerAddress
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private var credentials: TokenPair?
    private var refresh: Task<TokenPair, Error>?

    init(server: ServerAddress) {
        self.server = server
        let config = URLSessionConfiguration.ephemeral
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 12
        config.timeoutIntervalForResource = 20
        config.httpShouldSetCookies = false
        session = URLSession(configuration: config, delegate: RejectRedirects(), delegateQueue: nil)
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    deinit { session.invalidateAndCancel() }

    func hasSession() throws -> Bool {
        try Keychain.read(server.url.absoluteString) != nil
    }

    func pair(code: String) async throws -> Account {
        struct Body: Encodable { let code: String; let deviceName = "iPhone" }
        let data = try encoder.encode(Body(code: code))
        let tokens: TokenPair = try decode(await request("auth/pair", method: "POST", body: data))
        try store(tokens)
        return try await account()
    }

    func account() async throws -> Account { try await get("me") }

    func logout() async throws {
        _ = try await authorized("auth/logout", method: "POST")
        refresh?.cancel()
        try clear()
    }

    func dashboard(period: Period) async throws -> Dashboard {
        try await get("dashboard", query: ["period": period.rawValue])
    }

    func category(_ id: Int, period: Period) async throws -> CategoryDetail {
        try await get("categories/\(id)", query: ["period": period.rawValue])
    }

    func categoryChart(_ id: Int, range: ChartRange) async throws -> ChartResponse {
        try await get("categories/\(id)/chart", query: ["range": range.rawValue])
    }

    func product(_ id: Int) async throws -> ProductDetail { try await get("items/\(id)") }

    func productChart(_ id: Int, store: Int, range: ChartRange) async throws -> ChartResponse {
        try await get("items/\(id)/chart", query: ["store_id": String(store), "range": range.rawValue])
    }

    func update(_ id: Int, version: Int, quantity: Int, inTotal: Bool, key: UUID) async throws -> ProductDetail {
        struct Body: Encodable { let version: Int; let quantity: Int; let inTotal: Bool }
        let body = try encoder.encode(Body(version: version, quantity: quantity, inTotal: inTotal))
        return try decode(await authorized("items/\(id)", method: "PATCH", body: body, key: key))
    }

    func runStatus() async throws -> CheckRun { try await get("runs/current") }

    func changeRun(stop id: Int?, key: UUID) async throws -> CheckRun {
        let path = id.map { "runs/\($0)/stop" } ?? "runs"
        return try decode(await authorized(path, method: "POST", key: key))
    }

    private func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        try decode(await authorized(path, query: query))
    }

    private func authorized(
        _ path: String, query: [String: String] = [:], method: String = "GET",
        body: Data? = nil, key: UUID? = nil
    ) async throws -> Data {
        let token = try await accessToken()
        do {
            return try await request(path, query: query, method: method, body: body, key: key, token: token)
        } catch APIError.http(401, _) {
            if credentials?.accessToken == token { credentials = nil }
            let replacement = try await accessToken()
            do {
                return try await request(path, query: query, method: method, body: body, key: key, token: replacement)
            } catch APIError.http(401, _) {
                try clear()
                throw APIError.sessionExpired
            }
        }
    }

    private func accessToken() async throws -> String {
        if let credentials,
           let expiry = (try? Date.ISO8601FormatStyle().parse(credentials.accessExpiresAt))
                ?? (try? Date.ISO8601FormatStyle().time(includingFractionalSeconds: true).parse(credentials.accessExpiresAt)),
           expiry.timeIntervalSinceNow > 15 {
            return credentials.accessToken
        }
        if let refresh { return try await refresh.value.accessToken }
        guard let token = try Keychain.read(server.url.absoluteString) else { throw APIError.sessionExpired }
        let task = Task { try await rotate(token) }
        refresh = task
        return try await task.value.accessToken
    }

    private func rotate(_ token: String) async throws -> TokenPair {
        struct Body: Encodable { let refreshToken: String }
        defer { refresh = nil }
        let body = try encoder.encode(Body(refreshToken: token))
        do {
            let tokens: TokenPair = try decode(await request("auth/refresh", method: "POST", body: body))
            try Task.checkCancellation()
            try store(tokens)
            return tokens
        } catch APIError.http(401, _) {
            try clear()
            throw APIError.sessionExpired
        }
    }

    private func store(_ tokens: TokenPair) throws {
        try Keychain.write(tokens.refreshToken, account: server.url.absoluteString)
        credentials = tokens
    }

    private func clear() throws {
        credentials = nil
        try Keychain.remove(server.url.absoluteString)
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.invalidResponse }
    }

    private func request(
        _ path: String, query: [String: String] = [:], method: String = "GET",
        body: Data? = nil, key: UUID? = nil, token: String? = nil
    ) async throws -> Data {
        let url = server.url.appending(path: "api/v1/\(path)")
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        if !query.isEmpty {
            components?.queryItems = query.sorted(by: { $0.key < $1.key }).map {
                URLQueryItem(name: $0.key, value: $0.value)
            }
        }
        guard let endpoint = components?.url else { throw APIError.invalidServer }
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let key { request.setValue(key.uuidString, forHTTPHeaderField: "Idempotency-Key") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let error = try? decoder.decode(ServerErrorResponse.self, from: data).error
            if http.statusCode == 409, error?.code == "VERSION_CONFLICT" {
                throw APIError.conflict(error?.details?.current)
            }
            throw APIError.http(http.statusCode, error?.code)
        }
        return data
    }
}
