import SwiftUI

struct CategoryManager: View {
    let client: APIClient
    @Environment(WorkspaceNavigation.self) private var workspace
    @Environment(SessionModel.self) private var session
    @State private var catalog: CatalogCategories?
    @State private var edit: Operation?
    @State private var undo: String?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        List {
            if let error { LoadFailure(message: error) { Task { await load() } } }
            if let undo {
                Button("Отменить удаление", systemImage: "arrow.uturn.backward") {
                    apply("catalog/undo", body: ["token": undo])
                }
                .disabled(busy)
            }
            if let catalog {
                if catalog.categories.isEmpty {
                    ContentUnavailableView("Добавьте категорию", systemImage: "square.grid.2x2")
                }
                ForEach(catalog.categories) { category in
                    NavigationLink {
                        groups(category.id)
                    } label: {
                        LabeledContent(category.name, value: category.totalText)
                    }
                    .swipeActions {
                        Button("Удалить", role: .destructive) { edit = .removeCategory(category) }
                        Button("Изменить") { edit = .renameCategory(category) }.tint(PW.accent)
                    }
                    .contextMenu {
                        Button("Переименовать", systemImage: "pencil") { edit = .renameCategory(category) }
                        Button("Удалить", systemImage: "trash", role: .destructive) { edit = .removeCategory(category) }
                    }
                }
                .onMove { indices, destination in
                    var ids = catalog.categories.map(\.id)
                    ids.move(fromOffsets: indices, toOffset: destination)
                    apply("categories/order", method: "PUT", body: Reorder(ids: ids, version: catalog.orderVersion))
                }
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("Категории")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if catalog?.categories.isEmpty == false { EditButton() }
                Button("Добавить категорию", systemImage: "plus") { edit = .createCategory }
            }
        }
        .sheet(item: $edit) { operation in
            NavigationStack {
                CategoryOperationForm(operation: operation, alternatives: catalog?.categories ?? []) { name, keepItems, moveTo in
                    save(operation, name: name, keepItems: keepItems, moveTo: moveTo)
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    @ViewBuilder
    private func groups(_ id: Int) -> some View {
        if let category = catalog?.categories.first(where: { $0.id == id }) {
            List {
                if category.groups.isEmpty {
                    ContentUnavailableView("Групп нет", systemImage: "folder")
                }
                ForEach(category.groups) { group in
                    LabeledContent(group.name, value: "\(group.itemCount)")
                        .swipeActions {
                            Button("Удалить", role: .destructive) { edit = .removeGroup(group) }
                            Button("Изменить") { edit = .renameGroup(group) }.tint(PW.accent)
                        }
                        .contextMenu {
                            Button("Переименовать", systemImage: "pencil") { edit = .renameGroup(group) }
                            Button("Удалить", systemImage: "trash", role: .destructive) { edit = .removeGroup(group) }
                        }
                }
                .onMove { positions, destination in
                    var ids = category.groups.map(\.id)
                    ids.move(fromOffsets: positions, toOffset: destination)
                    apply("catalog/categories/\(id)/groups/order", method: "PUT", body: Reorder(ids: ids, version: category.version))
                }
                Button("Добавить группу", systemImage: "folder.badge.plus") { edit = .createGroup(id) }
            }
            .navigationTitle(category.name)
            .toolbar { if !category.groups.isEmpty { EditButton() } }
        }
    }

    enum Operation: Identifiable {
        case createCategory, renameCategory(CatalogCategory), removeCategory(CatalogCategory)
        case createGroup(Int), renameGroup(CatalogGroup), removeGroup(CatalogGroup)

        var id: String { title }
        var title: String {
            switch self {
            case .createCategory: "Новая категория"
            case .renameCategory: "Изменить категорию"
            case .removeCategory: "Удалить категорию"
            case .createGroup: "Новая группа"
            case .renameGroup: "Изменить группу"
            case .removeGroup: "Удалить группу"
            }
        }

        var name: String {
            switch self {
            case let .renameCategory(category), let .removeCategory(category): category.name
            case let .renameGroup(group), let .removeGroup(group): group.name
            case .createCategory, .createGroup: ""
            }
        }
    }

    private struct Rename: Encodable, Sendable { let name: String; let version: Int }
    private struct CategoryRemoval: Encodable, Sendable { let version: Int; let moveTo: Int? }
    private struct GroupRemoval: Encodable, Sendable { let version: Int; let keepItems: Bool }

    private func save(_ operation: Operation, name: String, keepItems: Bool, moveTo: Int?) {
        switch operation {
        case .createCategory:
            apply("catalog/categories", body: ["name": name])
        case let .renameCategory(category):
            apply("catalog/categories/\(category.id)", method: "PATCH", body: Rename(name: name, version: category.version))
        case let .removeCategory(category):
            apply("catalog/categories/\(category.id)/remove", body: CategoryRemoval(version: category.version, moveTo: moveTo))
        case let .createGroup(id):
            apply("catalog/categories/\(id)/groups", body: ["name": name])
        case let .renameGroup(group):
            apply("catalog/groups/\(group.id)", method: "PATCH", body: Rename(name: name, version: group.version))
        case let .removeGroup(group):
            apply("catalog/groups/\(group.id)/remove", body: GroupRemoval(version: group.version, keepItems: keepItems))
        }
    }

    private func apply(_ path: String, method: String = "POST", body: some Encodable & Sendable) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                let result: Mutation = try await client.send(path, method: method, body: body)
                undo = result.undoToken
                edit = nil
                workspace.changed()
                await load()
            } catch {
                if !session.checkSession(error) { self.error = error.localizedDescription }
                edit = nil
            }
        }
    }

    private func load() async {
        do {
            catalog = try await client.get("catalog/categories")
            error = nil
        } catch { if !session.checkSession(error) { self.error = error.localizedDescription } }
    }
}

private struct CategoryOperationForm: View {
    let operation: CategoryManager.Operation
    let alternatives: [CatalogCategory]
    let save: (String, Bool, Int?) -> Void
    @State private var name = ""
    @State private var keepItems = true
    @State private var moveTo: Int?

    var body: some View {
        Form {
            switch operation {
            case let .removeCategory(category):
                Section(category.name) {
                    Picker("Товары", selection: $moveTo) {
                        Text("Удалить вместе с категорией").tag(Int?.none)
                        ForEach(alternatives.filter { $0.id != category.id }) { Text("Перенести в «\($0.name)»").tag(Optional($0.id)) }
                    }
                    Button("Удалить категорию", role: .destructive) { save(name, keepItems, moveTo) }
                }
            case let .removeGroup(group):
                Section(group.name) {
                    Toggle("Оставить товары в категории", isOn: $keepItems)
                    Button("Удалить группу", role: .destructive) { save(name, keepItems, moveTo) }
                }
            default:
                TextField("Название", text: $name)
                Button("Сохранить") { save(name, keepItems, moveTo) }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .navigationTitle(operation.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { DismissToolbar() }
        .onAppear { name = operation.name }
    }
}
