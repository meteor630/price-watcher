import SwiftUI

struct ShoppingView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var period: Period = .month
    @State private var page: PurchasePage?
    @State private var entries: [PurchaseEntry] = []
    @State private var error: String?
    @State private var loadingMore = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Период", selection: $period) {
                        ForEach(Period.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if let page {
                        LabeledContent("Потрачено", value: page.spentText)
                        if let savings = page.savings { savingsRow(savings) }
                    }
                    NavigationLink { ArchiveView(client: client) } label: { Label("Архив", systemImage: "archivebox") }
                }
                if let error { LoadFailure(message: error) { Task { await load() } } }
                if entries.isEmpty, page != nil {
                    ContentUnavailableView("Покупок за этот период нет", systemImage: "basket")
                }
                if let page, !page.categories.isEmpty {
                    Section("По категориям") {
                        ForEach(page.categories) { spending in
                            NavigationLink {
                                CategoryView(id: spending.id, initialPeriod: period, client: client)
                            } label: {
                                VStack(alignment: .leading, spacing: 5) {
                                    ActivityRow(title: spending.category.name, subtitle: "Покупки · \(spending.count)", value: spending.spentText)
                                    if let savings = spending.savings { savingsRow(savings) }
                                }
                            }
                        }
                    }
                }
                if !entries.isEmpty {
                    Section("История покупок") {
                        ForEach(entries) { entry in
                            NavigationLink { ProductView(id: entry.id, client: client) } label: {
                                VStack(alignment: .leading, spacing: 5) {
                                    let details = [entry.category.name, entry.purchase.store?.name, Prices.date(entry.purchase.purchasedAt)]
                                        .compactMap { $0 }.joined(separator: " · ")
                                    ActivityRow(title: entry.purchase.name, subtitle: details, value: entry.purchase.totalText)
                                    if let savings = entry.savings { savingsRow(savings) }
                                }
                            }
                        }
                    }
                }
                if let cursor = page?.nextCursor {
                    Button("Показать ещё") { Task { await load(after: cursor) } }.disabled(loadingMore)
                }
            }
            .navigationTitle("Покупки")
            .task(id: "\(period.rawValue)-\(workspace.revision)") { await load() }
            .refreshable { await load() }
        }
    }

    private func savingsRow(_ savings: Savings) -> some View {
        Label(savings.savingsMinor < 0 ? "Переплата · \(savings.savingsText)" : "Экономия · \(savings.savingsText)",
              systemImage: savings.savingsMinor < 0 ? "arrow.up.right" : "arrow.down.right")
            .font(.caption).foregroundStyle(savings.savingsMinor < 0 ? PW.increase : PW.decrease)
    }

    private func load(after cursor: String? = nil) async {
        let requestedPeriod = period
        var query = ["period": requestedPeriod.rawValue]
        query["cursor"] = cursor
        loadingMore = cursor != nil
        defer { loadingMore = false }
        do {
            let next: PurchasePage = try await client.get("purchases", query: query)
            guard requestedPeriod == period, !Task.isCancelled else { return }
            entries = cursor == nil ? next.items : entries + next.items
            page = next
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private struct ArchiveView: View {
    let client: APIClient

    var body: some View {
        RemoteList<ArchiveEntry, _>(client: client, endpoint: "archive", title: "Архив", empty: "В архиве пока ничего нет") { entry in
            NavigationLink { ProductView(id: entry.id, client: client) } label: {
                ActivityRow(title: entry.item.name, subtitle: entry.category.name + " · " + Prices.date(entry.archivedAt))
            }
        }
    }
}

struct DashboardPanelView: View {
    let client: APIClient
    let panel: String
    let title: String
    let period: Period

    var body: some View {
        RemoteList<DashboardEntry, _>(client: client, endpoint: "dashboard/items", title: title, empty: "Товаров нет",
                                      query: ["panel": panel, "period": period.rawValue]) { entry in
            NavigationLink { ProductView(id: entry.id, client: client) } label: {
                ActivityRow(title: entry.name, subtitle: entry.category?.name, value: entry.newPriceText ?? entry.oldPriceText)
            }
        }
    }
}

struct ReviewListView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var busy = false
    @State private var error: String?
    @State private var undo: String?

    var body: some View {
        RemoteList<ReviewEntry, _>(client: client, endpoint: "reviews", title: "Проверка изменений", empty: "Нет изменений для проверки") { entry in
            VStack(alignment: .leading, spacing: 10) {
                NavigationLink { ProductView(id: entry.itemId, client: client) } label: {
                    ActivityRow(title: entry.itemName, subtitle: entry.store.name)
                }
                if !entry.beforeText.isEmpty { Text(entry.beforeText).foregroundStyle(.secondary) }
                if !entry.afterText.isEmpty { Text(entry.afterText) }
                HStack {
                    Button("Принять") { decide(entry, action: "accept") }.buttonStyle(.borderedProminent)
                    Button("Отклонить") { decide(entry, action: "reject") }.buttonStyle(.bordered)
                    Menu("Ещё") {
                        Button("Перепроверить") { decide(entry, action: "recheck") }
                        if let url = URL(string: entry.url) { Link("Открыть магазин", destination: url) }
                    }
                }
                .disabled(busy)
            }
            .padding(.vertical, 4)
        }
        .toolbar {
            if let undo {
                Button("Отменить", systemImage: "arrow.uturn.backward") {
                    run("catalog/undo", body: ["token": undo])
                }.disabled(busy)
            }
        }
        .alert("Не удалось сохранить", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("Закрыть") { error = nil }
        } message: { Text(error ?? "") }
    }

    private func decide(_ entry: ReviewEntry, action: String) {
        run("catalog/reviews/\(entry.id)", body: ["action": action])
    }

    private func run(_ path: String, body: [String: String]) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                let result: Mutation = try await client.send(path, body: body)
                undo = result.undoToken
                workspace.changed()
            } catch { self.error = error.localizedDescription }
        }
    }
}

struct NoticeListView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        RemoteList<Notice, _>(client: client, endpoint: "notifications", title: "Уведомления", empty: "Уведомлений нет") { notice in
            Button {
                if !notice.read { mark(["ids": [notice.id]]) }
            } label: {
                ActivityRow(title: notice.text, subtitle: Prices.date(notice.createdAt, withTime: true),
                            value: notice.read ? nil : "Новое")
            }
            .buttonStyle(.plain)
        }
        .toolbar {
            Button("Прочитать всё") { mark(["all": true]) }.disabled(busy)
        }
        .alert("Не удалось обновить уведомления", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("Закрыть") { error = nil }
        } message: { Text(error ?? "") }
    }

    private func mark(_ fields: some Encodable & Sendable) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                let _: Mutation = try await client.send("notifications/read", body: fields)
                workspace.changed()
            } catch { self.error = error.localizedDescription }
        }
    }
}

private struct RemoteList<Element: Decodable & Sendable & Identifiable, Row: View>: View {
    let client: APIClient
    let endpoint: String
    let title: String
    let empty: String
    var query: [String: String] = [:]
    @ViewBuilder let row: (Element) -> Row

    @Environment(WorkspaceNavigation.self) private var workspace
    @Environment(SessionModel.self) private var session
    @State private var items: [Element] = []
    @State private var cursor: String?
    @State private var loaded = false
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        List {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if loaded && items.isEmpty { ContentUnavailableView(empty, systemImage: "tray") }
            if !loaded && error == nil { ProgressView() }
            ForEach(items, content: row)
            if let cursor, !items.isEmpty {
                Button("Показать ещё") { Task { await load(after: cursor) } }.disabled(busy)
            }
        }
        .navigationTitle(title)
        .task(id: workspace.revision) { await load() }
        .refreshable { await load() }
    }

    private func load(after cursor: String? = nil) async {
        busy = true
        defer { busy = false }
        do {
            var query = query
            query["cursor"] = cursor
            let page: CollectionPage<Element> = try await client.get(endpoint, query: query)
            guard !Task.isCancelled else { return }
            items = cursor == nil ? page.items : items + page.items
            self.cursor = page.nextCursor
            error = nil
            loaded = true
        } catch { if !Task.isCancelled && !session.checkSession(error) { self.error = error.localizedDescription } }
    }
}

struct ActivityRow: View {
    let title: String
    var subtitle: String?
    var value: String?

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.body.weight(.medium))
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 0)
            if let value { Text(value).font(.subheadline.weight(.semibold)).monospacedDigit() }
        }
        .padding(.vertical, 3)
    }
}
