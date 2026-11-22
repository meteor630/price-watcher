import Observation
import SwiftUI

@main
struct PriceWatcherApp: App {
    @State private var session = SessionModel()

    var body: some Scene {
        WindowGroup {
            Group {
                if session.starting {
                    ProgressView("Подключаемся к PriceWatcher")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let account = session.account, let client = session.client {
                    HomeView(account: account, client: client)
                        .id(client.server.url)
                } else {
                    ConnectView()
                }
            }
            .background(PW.canvas)
            .preferredColorScheme(.dark)
            .tint(PW.accent)
            .environment(session)
            .task { await session.restore() }
        }
    }
}

@MainActor @Observable
final class SessionModel {
    private static let addressKey = "pricewatch.server"
    var address = UserDefaults.standard.string(forKey: SessionModel.addressKey) ?? SessionModel.initialAddress
    var starting = true
    var busy = false
    var canResume = false
    var message: String?
    var account: Account?
    var client: APIClient?

    private static var initialAddress: String {
        #if targetEnvironment(simulator)
        "http://localhost:8000"
        #else
        ""
        #endif
    }

    func restore() async {
        defer { starting = false }
        guard let server = try? ServerAddress(address) else { return }
        let api = APIClient(server: server)
        do {
            guard try await api.hasSession() else { return }
            canResume = true
            account = try await api.account()
            client = api
            canResume = false
            message = nil
        } catch {
            message = error.localizedDescription
            if case APIError.sessionExpired = error { canResume = false }
        }
    }

    func connect(_ code: String) async {
        guard !busy else { return }
        busy = true
        message = nil
        defer { busy = false }

        do {
            let server = try ServerAddress(address)
            let api = APIClient(server: server)
            address = server.url.absoluteString
            UserDefaults.standard.set(address, forKey: Self.addressKey)
            account = try await api.pair(code: code)
            client = api
            canResume = false
        } catch {
            message = error.localizedDescription
            if let server = try? ServerAddress(address) {
                canResume = (try? await APIClient(server: server).hasSession()) == true
            }
        }
    }

    func logout() async {
        guard !busy, let client else { return }
        busy = true
        message = nil
        defer { busy = false }
        do {
            try await client.logout()
            account = nil
            self.client = nil
        } catch {
            if !checkSession(error) { message = error.localizedDescription }
        }
    }

    @discardableResult
    func checkSession(_ error: Error) -> Bool {
        guard case APIError.sessionExpired = error else { return false }
        account = nil
        client = nil
        canResume = false
        message = error.localizedDescription
        return true
    }
}

struct ConnectView: View {
    @Environment(SessionModel.self) private var session
    @State private var code = ""

    private var normalizedCode: String {
        code.uppercased().filter { !$0.isWhitespace && $0 != "-" }
    }
    private var validCode: Bool { normalizedCode.wholeMatch(of: /[A-Z2-7]{8}/) != nil }
    private var server: ServerAddress? { try? ServerAddress(session.address) }

    var body: some View {
        @Bindable var session = session
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                HStack(spacing: 12) {
                    BrandMark(size: 46)
                    Text("PriceWatcher").font(.title3.weight(.semibold))
                }

                VStack(alignment: .leading, spacing: 12) {
                    Text("Подключение к серверу").font(.system(.largeTitle, design: .rounded, weight: .bold))
                        .accessibilityAddTraits(.isHeader)
                    Text("Подключите iPhone к своему серверу, чтобы видеть цены и историю покупок.")
                        .foregroundStyle(PW.muted)
                }
                .padding(.vertical, 12)

                VStack(alignment: .leading, spacing: 17) {
                    field("Адрес сервера") {
                        HStack {
                            TextField("http://192.168.1.20:8000", text: $session.address)
                                .keyboardType(.URL)
                                .textContentType(.URL)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .accessibilityLabel("Адрес сервера")
                            if !session.address.isEmpty {
                                Button {
                                    session.address = ""
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundStyle(PW.muted)
                                        .frame(minWidth: 44, minHeight: 44)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Очистить адрес")
                                .accessibilityIdentifier("clear-server")
                            }
                        }
                    }
                    field("Код подключения") {
                        TextField("8 символов", text: $code)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .textContentType(.oneTimeCode)
                            .fontDesign(.monospaced)
                            .accessibilityIdentifier("pair-code")
                    }
                    if let server {
                        Link(destination: server.profile) {
                            Label("Получить код на сайте", systemImage: "arrow.up.right")
                                .font(.subheadline.weight(.medium))
                        }
                    }
                }
                .card()

                if let message = session.message {
                    Label(message, systemImage: "exclamationmark.circle").foregroundStyle(PW.warning)
                        .font(.subheadline)
                }

                Button {
                    Task { await session.connect(normalizedCode) }
                } label: {
                    if session.busy {
                        ProgressView().tint(PW.accent).frame(maxWidth: .infinity)
                    } else {
                        Label("Подключить iPhone", systemImage: "arrow.right")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(PrimaryButton())
                .disabled(!validCode || server == nil || session.busy)
                .accessibilityIdentifier("connect-submit")

                if session.canResume {
                    Button("Восстановить подключение") {
                        Task {
                            session.starting = true
                            await session.restore()
                        }
                    }
                }
            }
            .padding(PW.pagePadding)
            .padding(.top, 26)
            .frame(maxWidth: 540)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private func field<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label).font(.subheadline.weight(.semibold)).foregroundStyle(PW.muted)
            content()
                .padding(13)
                .background(PW.canvas, in: RoundedRectangle(cornerRadius: 13))
        }
    }
}
