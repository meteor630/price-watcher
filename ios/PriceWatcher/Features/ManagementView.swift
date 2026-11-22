import Observation
import SwiftUI

enum WorkspacePage: Identifiable, Equatable {
    case newItem(category: Int?)
    case item(Int)
    case categories
    case profile

    var id: String {
        switch self {
        case let .newItem(category): "new-\(category ?? 0)"
        case let .item(id): "item-\(id)"
        case .categories: "categories"
        case .profile: "profile"
        }
    }
}

@MainActor @Observable
final class WorkspaceNavigation {
    enum Tab: Hashable { case overview, shopping, management }

    var tab: Tab = .overview
    var page: WorkspacePage?
    private(set) var revision = 0

    func open(_ page: WorkspacePage) { self.page = page }
    func changed() { revision += 1 }
}

struct PriceWatcherTabs: View {
    let account: Account
    let client: APIClient
    @State private var navigation = WorkspaceNavigation()

    var body: some View {
        @Bindable var navigation = navigation
        TabView(selection: $navigation.tab) {
            HomeView(client: client)
                .tabItem { Label("Обзор", systemImage: "chart.line.uptrend.xyaxis") }
                .tag(WorkspaceNavigation.Tab.overview)
            ShoppingView(client: client)
                .tabItem { Label("Покупки", systemImage: "basket") }
                .tag(WorkspaceNavigation.Tab.shopping)
            ManagementView(account: account, client: client)
                .tabItem { Label("Управление", systemImage: "slider.horizontal.3") }
                .tag(WorkspaceNavigation.Tab.management)
        }
        .sheet(item: $navigation.page) { page in
            NavigationStack {
                switch page {
                case let .newItem(category):
                    ProductEditor(client: client, itemID: nil, initialCategory: category)
                case let .item(id):
                    ProductEditor(client: client, itemID: id, initialCategory: nil)
                case .categories:
                    CategoryManager(client: client)
                        .toolbar { DismissToolbar() }
                case .profile:
                    NativeProfileView(account: account, client: client)
                        .toolbar { DismissToolbar() }
                }
            }
        }
        .environment(navigation)
    }
}

struct DismissToolbar: ToolbarContent {
    @Environment(\.dismiss) private var dismiss

    var body: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } }
    }
}

struct ManagementView: View {
    let account: Account
    let client: APIClient

    var body: some View {
        NavigationStack {
            List {
                Section("Каталог") {
                    NavigationLink { CatalogItemsView(client: client) } label: { Label("Товары", systemImage: "list.bullet.rectangle") }
                    NavigationLink { CategoryManager(client: client) } label: { Label("Категории и группы", systemImage: "square.grid.2x2") }
                    NavigationLink { ReviewListView(client: client) } label: { Label("Требуют решения", systemImage: "checkmark.bubble") }
                }
                if account.role == "admin" {
                    Section("Администрирование") {
                        NavigationLink { NoticeListView(client: client) } label: { Label("Уведомления", systemImage: "bell") }
                        NavigationLink { StoresView(client: client) } label: { Label("Магазины", systemImage: "storefront") }
                        NavigationLink { ScheduleView(client: client) } label: { Label("Проверка цен", systemImage: "clock") }
                        NavigationLink { UsersView(client: client) } label: { Label("Пользователи", systemImage: "person.2") }
                    }
                }
                Section("Аккаунт") {
                    NavigationLink { NativeProfileView(account: account, client: client) } label: { Label("Профиль", systemImage: "person.crop.circle") }
                }
            }
            .navigationTitle("Управление")
        }
    }
}

struct CatalogItemsView: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @Environment(SessionModel.self) private var session
    @State private var categories: [CatalogCategory] = []
    @State private var entries: [CatalogEntry] = []
    @State private var selection: Int?
    @State private var search = ""
    @State private var error: String?
    @State private var busy = false
    @State private var loaded = false

    private var shown: [CatalogEntry] {
        entries.filter {
            (selection == nil || $0.categoryId == selection)
                && (search.isEmpty || $0.product.name.localizedStandardContains(search))
        }
    }

    var body: some View {
        List {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if !categories.isEmpty {
                Picker("Категория", selection: $selection) {
                    Text("Все категории").tag(Int?.none)
                    ForEach(categories) { Text($0.name).tag(Optional($0.id)) }
                }
            }
            if loaded && shown.isEmpty {
                ContentUnavailableView("Товаров нет", systemImage: "tag")
            }
            ForEach(shown) { entry in
                NavigationLink {
                    ProductView(id: entry.id, client: client)
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.product.name)
                        Text([entry.categoryName, entry.groupName].compactMap { $0 }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .swipeActions {
                    Button("Изменить", systemImage: "pencil") { workspace.open(.item(entry.id)) }
                        .tint(PW.accent)
                }
                .contextMenu {
                    Button("Изменить", systemImage: "pencil") { workspace.open(.item(entry.id)) }
                    Button("Выше", systemImage: "arrow.up") { move(entry, by: -1) }
                        .disabled(entry.siblings.first == entry.id || busy)
                    Button("Ниже", systemImage: "arrow.down") { move(entry, by: 1) }
                        .disabled(entry.siblings.last == entry.id || busy)
                }
            }
        }
        .navigationTitle("Товары")
        .searchable(text: $search, prompt: "Название товара")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("Добавить товар", systemImage: "plus") {
                    workspace.open(categories.isEmpty ? .categories : .newItem(category: selection))
                }
                .accessibilityIdentifier("catalog-add-item")
            }
        }
        .task(id: workspace.revision) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do {
            async let catalog: CatalogCategories = client.get("catalog/categories")
            async let products: CollectionPage<CatalogEntry> = client.get("catalog/items")
            let (current, items) = try await (catalog, products)
            categories = current.categories
            entries = items.items
            if let selection, !categories.contains(where: { $0.id == selection }) { self.selection = nil }
            error = nil
            loaded = true
        } catch {
            if !Task.isCancelled && !session.checkSession(error) { self.error = error.localizedDescription }
        }
    }

    private func move(_ entry: CatalogEntry, by direction: Int) {
        guard !busy, let index = entry.siblings.firstIndex(of: entry.id),
              entry.siblings.indices.contains(index + direction) else { return }
        var order = entry.siblings
        order.swapAt(index, index + direction)
        let body = Reorder(ids: order, version: entry.parentVersion, categoryId: entry.categoryId, groupId: entry.groupId)
        busy = true
        Task {
            defer { busy = false }
            do {
                let _: Mutation = try await client.send("catalog/items/order", method: "PUT", body: body)
                workspace.changed()
            } catch { self.error = error.localizedDescription }
        }
    }
}
