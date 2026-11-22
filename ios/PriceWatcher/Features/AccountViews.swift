import SwiftUI

struct NativeProfileView: View {
    let account: Account
    let client: APIClient
    @Environment(SessionModel.self) private var session
    @State private var profile: Profile?
    @State private var pairing: Issued?
    @State private var telegram: Issued?
    @State private var deviceToRemove: Device?
    @State private var error: String?
    @State private var busy = false

    private struct Profile: Decodable, Sendable {
        let devices: [Device]
        let ownDeviceId: Int
        let telegramLinked: Bool
    }

    private struct Device: Decodable, Identifiable, Sendable {
        let deviceId: Int
        let name: String
        let lastSeenAt: String
        var id: Int { deviceId }
    }

    private struct Issued: Decodable, Sendable {
        let code: String
        let expiresAt: String
        let url: URL?
    }

    var body: some View {
        List {
            Section("Учётная запись") {
                Text(account.displayName)
                Text(client.server.url.absoluteString).font(.footnote).foregroundStyle(.secondary)
                NavigationLink("Сменить пароль") { PasswordView(client: client) }
            }
            if let error { LoadFailure(message: error) { Task { await load() } } }
            Section("Подключение") {
                Button("Получить код", systemImage: "qrcode") {
                    run { pairing = try await client.send("profile/pairing-code", body: EmptyPayload()) }
                }.disabled(busy)
                if let pairing {
                    ShareLink(item: pairing.code) {
                        LabeledContent("Код до \(Prices.date(pairing.expiresAt, withTime: true))", value: pairing.code)
                    }
                }
            }
            if let profile {
                Section("Устройства") {
                    ForEach(profile.devices) { device in
                        ActivityRow(title: device.name, subtitle: Prices.date(device.lastSeenAt, withTime: true),
                                    value: device.id == profile.ownDeviceId ? "Это устройство" : nil)
                        .swipeActions {
                            if device.id != profile.ownDeviceId {
                                Button("Отозвать", role: .destructive) { deviceToRemove = device }
                            }
                        }
                    }
                }
                Section("Telegram") {
                    if profile.telegramLinked {
                        Button("Отвязать Telegram", role: .destructive) {
                            run {
                                let _: Mutation = try await client.send("profile/telegram", method: "DELETE", body: EmptyPayload())
                                await load()
                            }
                        }.disabled(busy)
                    } else {
                        Button("Подключить Telegram") {
                            run { telegram = try await client.send("profile/telegram/code", body: EmptyPayload()) }
                        }.disabled(busy)
                        if let telegram {
                            if let url = telegram.url { Link("Открыть бота", destination: url) }
                            ShareLink(item: telegram.code) { Label(telegram.code, systemImage: "square.and.arrow.up") }
                        }
                    }
                }
            }
            Section {
                Button("Выйти", role: .destructive) { Task { await session.logout() } }
                    .disabled(session.busy)
            }
        }
        .navigationTitle("Профиль")
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog("Отозвать доступ устройства?", isPresented: Binding(
            get: { deviceToRemove != nil }, set: { if !$0 { deviceToRemove = nil } }
        ), titleVisibility: .visible, presenting: deviceToRemove) { device in
            Button("Отозвать", role: .destructive) {
                run {
                    let _: Mutation = try await client.send("profile/devices/\(device.id)", method: "DELETE", body: EmptyPayload())
                    await load()
                }
            }
        }
    }

    private func load() async {
        do {
            profile = try await client.get("profile")
            error = nil
        } catch { if !session.checkSession(error) { self.error = error.localizedDescription } }
    }

    private func run(_ action: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true
        error = nil
        Task {
            defer { busy = false }
            do { try await action() }
            catch { if !session.checkSession(error) { self.error = error.localizedDescription } }
        }
    }
}

private struct PasswordView: View {
    let client: APIClient
    @Environment(SessionModel.self) private var session
    @State private var current = ""
    @State private var password = ""
    @State private var repeatPassword = ""
    @State private var error: String?
    @State private var saving = false

    var body: some View {
        Form {
            Section {
                SecureField("Текущий пароль", text: $current).textContentType(.password)
                SecureField("Новый пароль", text: $password).textContentType(.newPassword)
                SecureField("Повтор нового пароля", text: $repeatPassword).textContentType(.newPassword)
            } footer: {
                Text("После смены пароля подключите iPhone заново.")
            }
            if let error { Text(error).foregroundStyle(PW.warning) }
            Button("Сменить пароль") {
                saving = true
                Task {
                    defer { saving = false }
                    do {
                        try await client.changePassword(current: current, new: password, repeat: repeatPassword)
                        _ = session.checkSession(APIError.sessionExpired)
                    } catch { self.error = error.localizedDescription }
                }
            }
            .disabled(current.isEmpty || password.isEmpty || password != repeatPassword || saving)
        }
        .navigationTitle("Пароль")
    }
}

struct StoresView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var page: Page?
    @State private var selection: ManagedStore?
    @State private var confirmation: StoreAction?
    @State private var error: String?
    @State private var busy = false

    private struct Page: Decodable, Sendable {
        let items: [ManagedStore]
        let colors: [String]
    }

    private struct StoreAction {
        let store: ManagedStore
        let remove: Bool
        var title: String { remove ? "Удалить магазин" : store.enabled ? "Отключить магазин" : "Включить магазин" }
    }

    var body: some View {
        List {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if let page {
                ForEach(page.items) { store in
                    Button { selection = store } label: {
                        ActivityRow(title: store.displayName, subtitle: store.canonicalHost,
                                    value: store.enabled ? "\(store.linkCount) ссылок" : "Отключён")
                    }
                    .buttonStyle(.plain)
                    .swipeActions {
                        if !store.used {
                            Button("Удалить", role: .destructive) { confirmation = StoreAction(store: store, remove: true) }.disabled(busy)
                        }
                        Button(store.enabled ? "Отключить" : "Включить") { confirmation = StoreAction(store: store, remove: false) }
                            .tint(PW.accent).disabled(busy)
                    }
                }
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("Магазины")
        .task { await load() }
        .refreshable { await load() }
        .sheet(item: $selection) { store in
            NavigationStack {
                StoreEditor(client: client, store: store, colors: page?.colors ?? []) {
                    workspace.changed()
                    await load()
                }
            }
        }
        .confirmationDialog(confirmation?.title ?? "", isPresented: Binding(
            get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }
        ), titleVisibility: .visible, presenting: confirmation) { action in
            Button(action.title, role: action.remove ? .destructive : nil) { save(action) }
        }
    }

    private func load() async {
        do {
            page = try await client.get("manage/stores")
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    private func save(_ action: StoreAction) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                let path = "manage/stores/\(action.store.id)"
                if action.remove {
                    let _: Mutation = try await client.send(path, method: "DELETE", body: EmptyPayload())
                } else {
                    let _: Mutation = try await client.send(path + "/enabled", method: "PUT", body: ["enabled": !action.store.enabled])
                }
                workspace.changed()
                await load()
            } catch { self.error = error.localizedDescription }
        }
    }
}

private struct ManagedStore: Decodable, Identifiable, Sendable {
    let storeId: Int
    let canonicalHost: String
    let displayName: String
    let colorToken: String
    let enabled: Bool
    let used: Bool
    let linkCount: Int
    var id: Int { storeId }
}

private struct StoreEditor: View {
    let client: APIClient
    let store: ManagedStore
    let colors: [String]
    let updated: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var color = ""
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Form {
            LabeledContent("Сайт", value: store.canonicalHost)
            TextField("Название", text: $name)
            Picker("Цвет", selection: $color) {
                ForEach(Array(colors.enumerated()), id: \.element) { index, token in
                    let swatches: [Color] = [.indigo, .blue, .teal, .pink, .purple, .yellow, .orange, .gray]
                    Label("Цвет \(index + 1)", systemImage: "circle.fill")
                        .foregroundStyle(swatches[index % swatches.count]).tag(token)
                }
            }
            if let error { Text(error).foregroundStyle(PW.warning) }
        }
        .navigationTitle("Магазин")
        .toolbar {
            DismissToolbar()
            ToolbarItem(placement: .confirmationAction) {
                Button("Сохранить") {
                    busy = true
                    Task {
                        defer { busy = false }
                        do {
                            let _: Mutation = try await client.send("manage/stores/\(store.id)", method: "PATCH", body: ["name": name, "color": color])
                            await updated()
                            dismiss()
                        } catch { self.error = error.localizedDescription }
                    }
                }.disabled(busy || name.isEmpty)
            }
        }
        .onAppear { name = store.displayName; color = store.colorToken }
    }
}

struct ScheduleView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var schedule: Schedule?
    @State private var error: String?
    @State private var busy = false

    private struct Schedule: Decodable, Sendable {
        var times: [String]
        var staleAfterDays: Int
        var titleSimilarityPercent: Int
        var priceJumpPercent: Int
        let versions: [String: Int]
        var nextRunAt: String?

    }

    var body: some View {
        Form {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if let schedule {
                Section("Ежедневная проверка") {
                    ForEach(0..<3) { index in
                        TextField(index == 0 ? "Время (ЧЧ:ММ)" : "Дополнительное время", text: time(index))
                            .keyboardType(.numbersAndPunctuation)
                    }
                    if let next = schedule.nextRunAt { LabeledContent("Следующая", value: Prices.date(next, withTime: true)) }
                }
                Section("Цены и уведомления") {
                    Stepper("Хранить свежесть цены · \(schedule.staleAfterDays) дн.", value: setting(\.staleAfterDays, fallback: 7), in: 1...30)
                    Stepper("Сходство названия · \(schedule.titleSimilarityPercent)%", value: setting(\.titleSimilarityPercent, fallback: 85), in: 50...99)
                    Stepper("Скачок цены · \(schedule.priceJumpPercent)%", value: setting(\.priceJumpPercent, fallback: 30), in: 5...200)
                }
                Button("Сохранить") { save(schedule) }.disabled(busy)
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("Проверка цен")
        .task { await load() }
    }

    private struct Payload: Encodable, Sendable {
        let times: [String]
        let staleAfterDays: Int
        let titleSimilarityPercent: Int
        let priceJumpPercent: Int
        let versions: [String: Int]
    }

    private func time(_ index: Int) -> Binding<String> {
        Binding {
            guard let times = schedule?.times, times.indices.contains(index) else { return "" }
            return times[index]
        } set: { value in
            guard var times = schedule?.times else { return }
            while times.count <= index { times.append("") }
            times[index] = value
            schedule?.times = times
        }
    }

    private func setting(_ key: WritableKeyPath<Schedule, Int>, fallback: Int) -> Binding<Int> {
        Binding(get: { schedule?[keyPath: key] ?? fallback }, set: { schedule?[keyPath: key] = $0 })
    }

    private func load() async {
        do {
            schedule = try await client.get("manage/schedule")
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    private func save(_ current: Schedule) {
        busy = true
        let input = Payload(times: current.times, staleAfterDays: current.staleAfterDays,
                            titleSimilarityPercent: current.titleSimilarityPercent, priceJumpPercent: current.priceJumpPercent, versions: current.versions)
        Task {
            defer { busy = false }
            do {
                schedule = try await client.send("manage/schedule", method: "PUT", body: input)
                workspace.changed()
                error = nil
            } catch { self.error = error.localizedDescription }
        }
    }
}

struct UsersView: View {
    let client: APIClient
    @State private var group: Users?
    @State private var issued: Invitation?
    @State private var action: UserAction?
    @State private var busy = false
    @State private var error: String?

    private struct Users: Decodable, Sendable {
        let users: [User]
        let invites: [Invite]
        let ownId: Int
    }

    private struct User: Decodable, Identifiable, Sendable {
        let userId: Int
        let login: String
        let displayName: String
        let role: String
        let disabledAt: String?
        var id: Int { userId }
    }

    private struct Invite: Decodable, Identifiable, Sendable {
        let inviteId: Int
        let expiresAt: String
        let role: String
        var id: Int { inviteId }
    }

    private struct Invitation: Decodable, Sendable { let url: URL }
    private struct UserAction { let id: Int; let key: String; let title: String }

    var body: some View {
        List {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if let group {
                Section("Аккаунты") {
                    ForEach(group.users) { user in
                        ActivityRow(title: user.displayName, subtitle: user.login,
                                    value: user.disabledAt != nil ? "Заблокирован" : user.role == "admin" ? "Админ" : nil)
                            .contextMenu {
                                if user.id != group.ownId {
                                    if user.disabledAt == nil {
                                        Button("Заблокировать", role: .destructive) {
                                            action = UserAction(id: user.id, key: "block", title: "Заблокировать пользователя")
                                        }
                                        if user.role == "admin" {
                                            Button("Снять права администратора") {
                                                action = UserAction(id: user.id, key: "demote", title: "Снять права администратора")
                                            }
                                        }
                                    } else {
                                        Button("Разблокировать") {
                                            action = UserAction(id: user.id, key: "unblock", title: "Разблокировать пользователя")
                                        }
                                    }
                                }
                            }
                    }
                }
                Section("Приглашения") {
                    Menu("Создать приглашение", systemImage: "person.badge.plus") {
                        Button("Пользователь") { invite("user") }
                        Button("Администратор") { invite("admin") }
                    }.disabled(busy)
                    if let issued { ShareLink(item: issued.url, subject: Text("Приглашение в PriceWatcher")) }
                    ForEach(group.invites) { invite in
                        ActivityRow(title: invite.role == "admin" ? "Администратор" : "Пользователь",
                                    subtitle: "До " + Prices.date(invite.expiresAt))
                            .swipeActions {
                                Button("Удалить", role: .destructive) {
                                    run {
                                        let _: Mutation = try await client.send("manage/invites/\(invite.id)", method: "DELETE", body: EmptyPayload())
                                        issued = nil
                                    }
                                }
                            }
                    }
                }
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("Пользователи")
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog(action?.title ?? "", isPresented: Binding(
            get: { action != nil }, set: { if !$0 { action = nil } }
        ), titleVisibility: .visible, presenting: action) { operation in
            Button("Подтвердить", role: operation.key == "block" ? .destructive : nil) {
                run { let _: Mutation = try await client.send("manage/users/\(operation.id)", body: ["action": operation.key]) }
            }
        }
    }

    private func invite(_ role: String) {
        run { issued = try await client.send("manage/invites", body: ["role": role]) }
    }

    private func run(_ operation: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                try await operation()
                await load()
            } catch { self.error = error.localizedDescription }
        }
    }

    private func load() async {
        do {
            group = try await client.get("manage/users")
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}
