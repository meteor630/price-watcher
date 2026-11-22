import SwiftUI

struct ProductEditor: View {
    let client: APIClient
    let itemID: Int?
    let initialCategory: Int?
    @Environment(WorkspaceNavigation.self) private var workspace
    @Environment(SessionModel.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var draft: ItemDraft?
    @State private var product: ProductDetail?
    @State private var categories: [CatalogCategory] = []
    @State private var links: [CatalogLink] = []
    @State private var linkText = ""
    @State private var pending = false
    @State private var error: String?
    @State private var undo: String?
    @State private var deleted = false
    @State private var confirm: Transition?

    private enum Transition: String, Identifiable {
        case remove, archive, restore

        var id: Self { self }
        var label: String {
            switch self {
            case .remove: "Удалить товар"
            case .archive: "В архив"
            case .restore: "Вернуть в список"
            }
        }

        var path: String { self == .restore ? "return" : rawValue }
    }

    var body: some View {
        Group {
            if let draft {
                Form {
                    if let error { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(PW.warning) }
                    if let undo {
                        Section {
                            Button("Отменить действие", systemImage: "arrow.uturn.backward") {
                                run { try await revert(undo) }
                            }
                        }
                    }
                    if deleted {
                        ContentUnavailableView("Товар удалён", systemImage: "trash")
                    } else {
                        fields(draft)
                        linkFields
                        if let product { actions(product) }
                    }
                }
            } else if let error {
                LoadFailure(message: error) { Task { await load() } }.padding()
            } else {
                ProgressView()
            }
        }
        .navigationTitle(itemID == nil && product == nil ? "Новый товар" : "Изменить товар")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            DismissToolbar()
            ToolbarItem(placement: .confirmationAction) {
                if let draft, !deleted {
                    Button(product == nil ? "Добавить" : "Сохранить") { run { try await save(draft) } }
                        .disabled(draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || pending)
                        .accessibilityIdentifier("editor-save")
                }
            }
        }
        .confirmationDialog(confirm?.label ?? "", isPresented: Binding(
            get: { confirm != nil },
            set: { if !$0 { confirm = nil } }
        ), titleVisibility: .visible, presenting: confirm) { action in
            Button(action.label, role: action == .remove ? .destructive : nil) {
                run { try await transition(action) }
            }
        }
        .task { await load() }
    }

    private func fields(_ value: ItemDraft) -> some View {
        Section("Товар") {
            TextField("Короткое имя", text: binding(\.name, fallback: value.name))
                .accessibilityIdentifier("editor-name")
            TextField("Полное название", text: binding(\.fullName, fallback: value.fullName))
            Stepper("Количество · \(value.quantity)", value: binding(\.quantity, fallback: value.quantity), in: 1...999)
            Picker("Категория", selection: Binding(get: { self.draft?.categoryId ?? value.categoryId }, set: { new in
                self.draft?.categoryId = new
                self.draft?.groupId = nil
            })) {
                ForEach(categories) { Text($0.name).tag($0.id) }
            }
            let groups = categories.first(where: { $0.id == value.categoryId })?.groups ?? []
            if !groups.isEmpty {
                Picker("Группа", selection: binding(\.groupId, fallback: value.groupId)) {
                    Text("Без группы").tag(Int?.none)
                    ForEach(groups) { Text($0.name).tag(Optional($0.id)) }
                }
            }
        }
    }

    private var linkFields: some View {
        Section("Ссылки магазинов") {
            ForEach(links) { link in
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        if let url = URL(string: link.url) {
                            Link(link.storeName, destination: url)
                        } else {
                            Text(link.storeName)
                        }
                        Text(link.priceText ?? linkStatus(link.status))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Убрать ссылку", systemImage: "minus.circle") {
                        run { try await remove(link) }
                    }
                    .labelStyle(.iconOnly).buttonStyle(.borderless).disabled(pending)
                }
            }
            TextField("Вставить ссылки", text: $linkText, axis: .vertical)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
            if product != nil {
                Button("Добавить ссылки", systemImage: "link.badge.plus") { run { try await addLinks() } }
                    .disabled(linkLines.isEmpty || pending)
            }
        }
    }

    private func actions(_ product: ProductDetail) -> some View {
        Section {
            if product.state == "active" || product.state == nil {
                NavigationLink("Куплено") {
                    PurchaseEditor(client: client, itemID: product.id) {
                        Task {
                            do { try await loadProduct(product.id) }
                            catch { self.error = error.localizedDescription }
                        }
                    }
                }
                Button("В архив", systemImage: "archivebox") { confirm = .archive }.disabled(pending)
            } else {
                Button("Вернуть в список", systemImage: "arrow.uturn.backward") { confirm = .restore }.disabled(pending)
            }
            Button("Удалить товар", systemImage: "trash", role: .destructive) { confirm = .remove }
                .disabled(pending)
                .accessibilityIdentifier("editor-delete")
        }
    }

    private var linkLines: [String] {
        linkText.split(whereSeparator: \.isNewline).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty }
    }

    private func binding<Value>(_ path: WritableKeyPath<ItemDraft, Value>, fallback: Value) -> Binding<Value> {
        Binding(get: { draft?[keyPath: path] ?? fallback }, set: { draft?[keyPath: path] = $0 })
    }

    private func load() async {
        do {
            let response: CatalogCategories = try await client.get("catalog/categories")
            categories = response.categories
            guard let category = categories.first(where: { $0.id == initialCategory }) ?? categories.first else {
                error = "Сначала добавьте категорию."
                return
            }
            if let id = product?.id ?? itemID {
                try await loadProduct(id)
            } else {
                draft = ItemDraft(categoryId: category.id)
            }
            error = nil
        } catch {
            if !session.checkSession(error) { self.error = error.localizedDescription }
        }
    }

    private func loadProduct(_ id: Int) async throws {
        async let current = client.product(id)
        async let storeLinks: CollectionPage<CatalogLink> = client.get("catalog/items/\(id)/links")
        let (product, links) = try await (current, storeLinks)
        guard let category = product.category else { throw APIError.invalidResponse }
        self.product = product
        self.links = links.items
        draft = ItemDraft(name: product.name, fullName: product.fullName ?? "", categoryId: category.id,
                          groupId: product.group?.id, quantity: product.quantity, version: product.version)
    }

    private func save(_ original: ItemDraft) async throws {
        var values = original
        if let product {
            let _: Mutation = try await client.send("catalog/items/\(product.id)", method: "PATCH", body: values)
            workspace.changed()
            dismiss()
        } else {
            values.links = linkLines
            let result: Mutation = try await client.send("catalog/items", body: values)
            guard let id = result.id else { throw APIError.invalidResponse }
            workspace.changed()
            if let failures = result.results?.compactMap(\.error), !failures.isEmpty {
                try await loadProduct(id)
                error = failures.joined(separator: "\n")
            } else {
                dismiss()
            }
        }
    }

    private func addLinks() async throws {
        guard let id = product?.id else { return }
        let result: Mutation = try await client.send("catalog/items/\(id)/links", body: ["links": linkLines])
        linkText = ""
        workspace.changed()
        let page: CollectionPage<CatalogLink> = try await client.get("catalog/items/\(id)/links")
        links = page.items
        if let messages = result.results?.compactMap(\.error), !messages.isEmpty { error = messages.joined(separator: "\n") }
    }

    private func remove(_ link: CatalogLink) async throws {
        guard let id = product?.id else { return }
        let result: Mutation = try await client.send("catalog/items/\(id)/links/\(link.id)", method: "DELETE", body: EmptyPayload())
        links.removeAll { $0.id == link.id }
        undo = result.undoToken
        workspace.changed()
    }

    private func transition(_ action: Transition) async throws {
        guard let product else { return }
        let result: Mutation = try await client.send("catalog/items/\(product.id)/\(action.path)", body: VersionPayload(version: product.version))
        undo = result.undoToken
        deleted = action == .remove
        workspace.changed()
        if !deleted { try await loadProduct(product.id) }
    }

    private func revert(_ token: String) async throws {
        let result: Mutation = try await client.send("catalog/undo", body: ["token": token])
        undo = nil
        deleted = false
        workspace.changed()
        if let id = product?.id ?? result.id { try await loadProduct(id) }
    }

    private func run(_ action: @escaping () async throws -> Void) {
        guard !pending else { return }
        pending = true
        error = nil
        Task {
            defer { pending = false }
            do { try await action() }
            catch { if !session.checkSession(error) { self.error = error.localizedDescription } }
        }
    }

    private func linkStatus(_ key: String) -> String {
        switch key {
        case "checking": "Проверяем"
        case "not_found": "Цена не найдена"
        case "store_disabled": "Магазин отключён"
        default: "Ожидает проверки"
        }
    }
}

struct PurchaseEditor: View {
    let client: APIClient
    let itemID: Int
    let updated: () -> Void
    @Environment(WorkspaceNavigation.self) private var workspace
    @Environment(\.dismiss) private var dismiss
    @State private var details: PurchaseDefaults?
    @State private var price = ""
    @State private var quantity = 1
    @State private var storeId: Int?
    @State private var date = Date()
    @State private var error: String?
    @State private var busy = false
    @State private var undo: String?
    @State private var changed = false

    private struct Fields: Encodable, Sendable {
        let version: Int
        let price: String
        let quantity: Int
        let storeId: Int?
        let day: String
    }

    var body: some View {
        Form {
            if let error { Label(error, systemImage: "exclamationmark.circle") }
            if let undo {
                Section("Покупка записана") {
                    Button("Отменить покупку") { save(undo: undo) }
                    Button("Готово") { dismiss() }
                }
            } else if let details {
                Section(details.name) {
                    TextField("Цена за штуку", text: $price).keyboardType(.decimalPad)
                    Stepper("Количество · \(quantity)", value: $quantity, in: 1...999)
                    Picker("Магазин", selection: $storeId) {
                        Text("Не указан").tag(Int?.none)
                        ForEach(details.stores) { Text($0.name).tag(Optional($0.id)) }
                    }
                    DatePicker("Дата", selection: $date, in: ...Date(), displayedComponents: .date)
                    Button("Записать покупку") { save() }.disabled(busy || price.isEmpty)
                }
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle("Покупка")
        .onDisappear { if changed { updated() } }
        .task {
            do {
                let form: PurchaseDefaults = try await client.get("catalog/items/\(itemID)/purchase")
                price = form.priceInput
                quantity = form.quantity
                storeId = form.storeId
                details = form
            } catch { self.error = error.localizedDescription }
        }
    }

    private func save(undo: String? = nil) {
        guard !busy, let details else { return }
        busy = true
        error = nil
        Task {
            defer { busy = false }
            do {
                if let undo {
                    let _: Mutation = try await client.send("catalog/undo", body: ["token": undo])
                    changed = true
                    dismiss()
                } else {
                    let format = DateFormatter()
                    format.calendar = Calendar(identifier: .gregorian)
                    format.locale = Locale(identifier: "en_US_POSIX")
                    format.dateFormat = "yyyy-MM-dd"
                    let day = format.string(from: date)
                    let fields = Fields(version: details.version, price: price, quantity: quantity, storeId: storeId, day: day)
                    let response: Mutation = try await client.send("catalog/items/\(itemID)/purchase", body: fields)
                    self.undo = response.undoToken
                    changed = true
                }
                workspace.changed()
            } catch { self.error = error.localizedDescription }
        }
    }
}
