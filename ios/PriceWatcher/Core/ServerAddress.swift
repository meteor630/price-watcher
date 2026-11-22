import Foundation

struct ServerAddress: Sendable, Equatable {
    let url: URL

    init(_ input: String) throws {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var parts = URLComponents(string: value),
              let scheme = parts.scheme?.lowercased(),
              let host = parts.host?.lowercased(), !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              ["", "/", "/api/v1", "/api/v1/"].contains(parts.path),
              parts.port.map({ (1...65535).contains($0) }) != false
        else { throw APIError.invalidServer }
        guard scheme == "https" || (scheme == "http" && Self.isLocal(host)) else {
            throw APIError.insecureServer
        }
        parts.scheme = scheme
        parts.host = host
        parts.path = ""
        if parts.port == (scheme == "https" ? 443 : 80) { parts.port = nil }
        guard let url = parts.url else { throw APIError.invalidServer }
        self.url = url
    }

    private static func isLocal(_ host: String) -> Bool {
        if host == "localhost" || host.hasSuffix(".local") { return true }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        let numbers = parts.compactMap { UInt8($0) }
        guard parts.count == 4, numbers.count == 4,
              numbers.map(String.init).joined(separator: ".") == host
        else { return false }
        return numbers[0] == 127 || numbers[0] == 10
            || (numbers[0] == 172 && (16...31).contains(numbers[1]))
            || (numbers[0] == 192 && numbers[1] == 168)
    }

    var profile: URL { url.appending(path: "web/settings/profile") }

    func media(_ path: String?) -> URL? {
        guard let path, let components = URLComponents(string: path),
              components.host == nil, components.scheme == nil,
              components.path.hasPrefix("/media/"), components.query == nil,
              components.fragment == nil,
              !components.path.split(separator: "/").contains("..")
        else { return nil }
        return URL(string: path, relativeTo: url)?.absoluteURL
    }
}
