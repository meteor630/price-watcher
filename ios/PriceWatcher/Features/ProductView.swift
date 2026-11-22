import SwiftUI

struct ProductView: View {
    let id: Int
    let client: APIClient
    @Environment(SessionModel.self) private var session
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var product: ProductDetail?
    @State private var points: [PricePoint]?
    @State private var selectedStore: Int?
    @State private var range: ChartRange = .month
    @State private var error: String?
    @State private var chartError: String?
    @State private var saving = false
    @State private var updateError: String?
    @State private var quantity = 1
    @State private var inTotal = false
    @State private var pendingEdit: PendingEdit?

    private struct ChartSelection: Hashable {
        let store: Int?
        let range: ChartRange
    }

    private struct PendingEdit {
        let version: Int
        let quantity: Int
        let inTotal: Bool
        let key: UUID
    }

    private var selection: ChartSelection { ChartSelection(store: selectedStore, range: range) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 21) {
                if let error { LoadFailure(message: error) { Task { await load() } } }
                if let product {
                    overview(product)
                    if product.state == "active" || product.state == nil { editCard(product) }
                    if !product.offers.isEmpty {
                        VStack(alignment: .leading, spacing: 11) {
                            SectionTitle("Магазины")
                            ForEach(product.offers) { offer in storeCard(offer) }
                        }
                        historyChart(product.offers)
                    }
                    history(product.history)
                } else if error == nil {
                    ProgressView("Загружаем товар").frame(maxWidth: .infinity).padding()
                }
            }
            .page()
        }
        .background(PW.canvas)
        .navigationTitle("Товар")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Действия с товаром", systemImage: "ellipsis.circle") {
                    workspace.open(.item(id))
                }
                .labelStyle(.iconOnly)
            }
        }
        .task(id: workspace.revision) { await load() }
        .task(id: selection) { await loadChart() }
        .refreshable {
            await load()
            await loadChart()
        }
    }

    private func overview(_ product: ProductDetail) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            if let photo = client.server.media(product.photoUrl) {
                AsyncImage(url: photo) { image in
                    image.resizable().scaledToFit()
                } placeholder: {
                    Image(systemName: "photo").foregroundStyle(PW.muted)
                        .frame(maxWidth: .infinity, minHeight: 64)
                }
                .frame(maxWidth: .infinity, maxHeight: 190)
                .clipShape(RoundedRectangle(cornerRadius: 13))
                .accessibilityHidden(true)
            }
            Text(product.name).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            if let fullName = product.fullName, fullName != product.name {
                Text(fullName).font(.subheadline).foregroundStyle(PW.muted)
            }
            Text(product.unitPriceText ?? "Цена пока неизвестна")
                .font(.system(.largeTitle, design: .rounded, weight: .bold))
                .foregroundStyle(PW.text).monospacedDigit()
            if let regular = product.regularPriceText {
                Text("Без карты · \(regular)").font(.subheadline).foregroundStyle(PW.muted)
            }
            if let best = product.bestStore {
                Label(best.name, systemImage: "storefront").font(.subheadline).foregroundStyle(PW.muted)
            }
            if let status = Prices.status(product.priceStatus, underReview: product.underReview) {
                Label(status, systemImage: "exclamationmark.circle")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(PW.warning)
            }
            if let failure = product.failureReason { Text(failure).font(.caption).foregroundStyle(PW.muted) }
            if let purchase = product.purchase {
                Text("Куплено \(Prices.date(purchase.purchasedAt)) · \(purchase.totalText)")
                    .font(.subheadline.weight(.semibold))
            } else if product.state == "archived" {
                Label("В архиве", systemImage: "archivebox").font(.subheadline).foregroundStyle(PW.muted)
            }
        }
        .card()
    }

    private func editCard(_ product: ProductDetail) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            SectionTitle("В списке покупок")
            Stepper("Количество · \(quantity)", value: $quantity, in: 1...999)
                .disabled(saving)
                .accessibilityIdentifier("product-quantity")
            Toggle("Учитывать в итоге", isOn: $inTotal).disabled(saving)
            if let updateError { Text(updateError).font(.subheadline).foregroundStyle(PW.warning) }
            if saving { ProgressView("Сохраняем…") }
            if quantity != product.quantity || inTotal != product.inTotal {
                Button("Сохранить") { Task { await save(product) } }
                    .buttonStyle(PrimaryButton()).disabled(saving)
                    .accessibilityIdentifier("product-save")
            }
        }
        .card()
    }

    private func storeCard(_ offer: StoreOffer) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(offer.store.name).font(.headline)
                Spacer()
                Text(offer.cardPriceText ?? offer.priceText ?? "—")
                    .font(.headline).monospacedDigit().foregroundStyle(offer.isBest ? PW.accent : .primary)
            }
            if offer.cardPriceText != nil, let price = offer.priceText {
                Text("Без карты · \(price)").font(.caption).foregroundStyle(PW.muted)
            }
            if let status = Prices.status(offer.priceStatus) {
                Text(status).font(.caption).foregroundStyle(PW.warning)
            }
            if let checked = offer.checkedAt {
                Text("Проверено \(Prices.date(checked, withTime: true))").font(.caption).foregroundStyle(PW.muted)
            }
            if let url = URL(string: offer.url), ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
               url.host != nil, url.user == nil, url.password == nil {
                Link(destination: url) {
                    Label("Открыть в магазине", systemImage: "arrow.up.right")
                        .font(.subheadline).frame(minHeight: 44, alignment: .leading)
                }
            }
        }
        .card()
    }

    private func historyChart(_ offers: [StoreOffer]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle("Динамика цены")
            Picker("Магазин", selection: $selectedStore) {
                ForEach(offers) { offer in Text(offer.store.name).tag(Optional(offer.store.id)) }
            }
            .pickerStyle(.menu)
            SelectionStrip(selection: $range, title: \.title)
            if let chartError {
                LoadFailure(message: chartError) { Task { await loadChart() } }
            } else if let points {
                PriceChart(points: points).id(selection)
            } else {
                ProgressView().frame(maxWidth: .infinity).padding()
            }
        }
        .card()
    }

    private func history(_ events: [HistoryEvent]) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            SectionTitle("События")
            if events.isEmpty {
                Text("Истории пока нет").foregroundStyle(PW.muted)
            } else {
                ForEach(Array(events.enumerated()), id: \.offset) { index, event in
                    if index > 0 { Divider() }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(event.text).font(.subheadline.weight(.medium))
                        Text("\(event.store.name) · \(Prices.date(event.at, withTime: true))")
                            .font(.caption).foregroundStyle(PW.muted)
                    }
                }
            }
        }
        .card()
    }

    private func load() async {
        guard !saving else { return }
        do {
            let detail = try await client.product(id)
            guard !Task.isCancelled, !saving else { return }
            let editing = product.map { quantity != $0.quantity || inTotal != $0.inTotal } ?? false
            apply(detail, resetEdit: !editing)
            error = nil
        } catch {
            guard !Task.isCancelled else { return }
            if !session.checkSession(error) { self.error = error.localizedDescription }
        }
    }

    private func apply(_ detail: ProductDetail, resetEdit: Bool = true) {
        product = detail
        if resetEdit {
            quantity = detail.quantity
            inTotal = detail.inTotal
        }
        if !detail.offers.contains(where: { $0.store.id == selectedStore }) {
            selectedStore = detail.offers.first(where: \.isBest)?.store.id ?? detail.offers.first?.store.id
        }
    }

    private func loadChart() async {
        let requested = selection
        chartError = nil
        points = nil
        guard let store = requested.store else { return }
        do {
            let response = try await client.productChart(id, store: store, range: requested.range)
            guard !Task.isCancelled, selection == requested else { return }
            points = response.points
        } catch {
            guard !Task.isCancelled, selection == requested else { return }
            if !session.checkSession(error) { chartError = error.localizedDescription }
        }
    }

    private func save(_ current: ProductDetail) async {
        guard !saving else { return }
        saving = true
        updateError = nil
        defer { saving = false }

        if pendingEdit?.version != current.version || pendingEdit?.quantity != quantity || pendingEdit?.inTotal != inTotal {
            pendingEdit = PendingEdit(version: current.version, quantity: quantity, inTotal: inTotal, key: UUID())
        }
        guard let edit = pendingEdit else { return }

        do {
            apply(try await client.update(id, version: edit.version, quantity: edit.quantity, inTotal: edit.inTotal, key: edit.key))
            pendingEdit = nil
        } catch APIError.conflict(let latest) {
            pendingEdit = nil
            if let latest {
                apply(latest)
            } else if let fresh = try? await client.product(id) {
                apply(fresh)
            }
            updateError = APIError.conflict(latest).localizedDescription
        } catch {
            if case APIError.http = error { pendingEdit = nil }
            if !session.checkSession(error) { updateError = error.localizedDescription }
        }
    }
}
