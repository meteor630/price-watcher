import SwiftUI

private enum Route: Hashable {
    case category(Int, Period)
}

struct HomeView: View {
    let client: APIClient
    @Environment(SessionModel.self) private var session
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var period: Period = .day
    @State private var chartRange: ChartRange = .month
    @State private var chart: [PricePoint]?
    @State private var chartError: String?
    @State private var dashboard: Dashboard?
    @State private var run: CheckRun?
    @State private var error: String?
    @State private var runError: String?
    @State private var runBusy = false
    @State private var pendingRun: PendingRun?

    private struct PendingRun {
        let stopID: Int?
        let key: UUID
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    header
                    summary(dashboard?.period == period ? dashboard : nil)
                    if let error { LoadFailure(message: error) { Task { await load() } } }
                    if let dashboard, dashboard.period == period {
                        counts(dashboard)
                        if let count = dashboard.panels.tracked, count > 0 {
                            NavigationLink {
                                DashboardPanelView(client: client, panel: "tracked", title: "Отслеживаются", period: period)
                            } label: {
                                Label("Отслеживаются · \(count)", systemImage: "list.bullet.rectangle")
                                    .font(.subheadline.weight(.medium)).foregroundStyle(PW.accent)
                            }
                        }
                        if let count = dashboard.reviewsPending, count > 0 {
                            NavigationLink {
                                ReviewListView(client: client)
                            } label: {
                                Label("Требуют решения · \(count)", systemImage: "checkmark.bubble").font(.subheadline.weight(.medium))
                            }
                        }
                        if let run { runCard(run) }
                        SectionTitle("Категории")
                        if dashboard.categories.isEmpty {
                            ContentUnavailableView("Добавьте категорию", systemImage: "square.grid.2x2",
                                                   description: Text("Категории и товары с сайта появятся здесь."))
                                .card()
                        } else {
                            LazyVStack(spacing: 10) {
                                ForEach(dashboard.categories) { category in
                                    NavigationLink(value: Route.category(category.id, period)) {
                                        categoryCard(category)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        priceHistory
                    }
                }
                .page()
            }
            .background(PW.canvas)
            .navigationBarHidden(true)
            .navigationDestination(for: Route.self) { route in
                switch route {
                case let .category(id, period): CategoryView(id: id, initialPeriod: period, client: client)
                }
            }
            .task(id: "\(period.rawValue)-\(workspace.revision)") { await load() }
            .task(id: "\(chartRange.rawValue)-\(workspace.revision)") { await loadHistory() }
            .task(id: run?.active == true) { await trackRun() }
            .refreshable {
                async let summary: Void = load()
                async let trend: Void = loadHistory()
                _ = await (summary, trend)
            }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            BrandMark(size: 44)
            VStack(alignment: .leading, spacing: 3) {
                Text("PriceWatcher").font(.title3.weight(.semibold)).accessibilityAddTraits(.isHeader)
                if let dashboard {
                    Text("Цены на \(Prices.date(dashboard.asOf))")
                        .font(.caption).foregroundStyle(PW.muted)
                }
            }
            Spacer()
            Button {
                workspace.open(dashboard?.categories.isEmpty == true ? .categories : .newItem(category: nil))
            } label: {
                Image(systemName: "plus").font(.body.weight(.semibold))
                    .frame(width: 46, height: 46).background(PW.accent.opacity(0.16), in: Circle())
            }
            .accessibilityLabel("Добавить товар")
            .accessibilityIdentifier("overview-add-item")
            Button { workspace.open(.profile) } label: {
                Image(systemName: "person.crop.circle").font(.title2)
                    .frame(width: 46, height: 46)
                    .background(PW.surface, in: Circle())
            }
            .accessibilityLabel("Профиль")
        }
    }

    private func summary(_ value: Dashboard?) -> some View {
        let shape = RoundedRectangle(cornerRadius: 22)
        return VStack(alignment: .leading, spacing: 13) {
            Label("Общий итог", systemImage: "basket")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(PW.muted)

            if let value {
                Text(value.totalText)
                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                    .monospacedDigit()
                    .foregroundStyle(PW.text)
                    .accessibilityIdentifier("dashboard-total")
                ViewThatFits(in: .horizontal) {
                    HStack {
                        includedCount(value.inTotalCount)
                        Spacer(minLength: 8)
                        if let change = value.change { PriceDelta(change: change) }
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        includedCount(value.inTotalCount)
                        if let change = value.change { PriceDelta(change: change) }
                    }
                }
            } else if error == nil {
                ProgressView("Загружаем цены").frame(minHeight: 54)
            }

            SelectionStrip(selection: $period, title: \.title)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            shape.fill(LinearGradient(colors: [PW.heroSurface, PW.surface],
                                      startPoint: .topLeading, endPoint: .bottomTrailing))
        }
        .overlay { shape.strokeBorder(PW.stroke).allowsHitTesting(false) }
    }

    private func includedCount(_ count: Int) -> some View {
        Text("В итоге · \(count)").font(.footnote.weight(.medium)).foregroundStyle(PW.muted)
    }

    private func counts(_ value: Dashboard) -> some View {
        HStack(spacing: 8) {
            counter("Дешевле", panel: "cheaper", symbol: "arrow.down.right", value: value.panels.cheaper, color: PW.decrease)
            counter("Дороже", panel: "pricier", symbol: "arrow.up.right", value: value.panels.pricier, color: PW.increase)
            counter("Нет в наличии", panel: "out_of_stock", symbol: "minus.circle", value: value.panels.outOfStock, color: PW.muted)
        }
    }

    private func counter(_ title: String, panel: String, symbol: String, value: Int, color: Color) -> some View {
        NavigationLink {
            DashboardPanelView(client: client, panel: panel, title: title, period: period)
        } label: {
            VStack(alignment: .leading, spacing: 7) {
                Label(value.formatted(), systemImage: symbol)
                    .font(.subheadline.weight(.semibold)).foregroundStyle(color)
                Text(title).font(.caption.weight(.medium)).foregroundStyle(PW.muted)
            }
            .frame(maxWidth: .infinity, minHeight: 47, alignment: .leading)
            .padding(10)
            .background(PW.surfaceRaised, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }

    private var priceHistory: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle("Итог по дням")
            SelectionStrip(selection: $chartRange, title: \.title)
            if let chart {
                PriceChart(points: chart).id(chartRange)
            } else if let chartError {
                LoadFailure(message: chartError) { Task { await loadHistory() } }
            } else {
                ProgressView().frame(maxWidth: .infinity, minHeight: 100)
            }
        }
        .card()
    }

    private func loadHistory() async {
        let requested = chartRange
        chart = nil
        chartError = nil
        do {
            let response: ChartResponse = try await client.get("dashboard/chart", query: ["range": requested.rawValue])
            if !Task.isCancelled && requested == chartRange { chart = response.points }
        } catch {
            if !Task.isCancelled && requested == chartRange && !session.checkSession(error) { chartError = error.localizedDescription }
        }
    }

    private func categoryCard(_ category: CategorySummary) -> some View {
        HStack(spacing: 13) {
            Image(systemName: "square.stack").font(.system(size: 16, weight: .semibold))
                .foregroundStyle(PW.accent)
                .frame(width: 38, height: 38)
                .background(PW.accentSurface, in: RoundedRectangle(cornerRadius: 12))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(category.name).font(.headline)
                Text("Отслеживается · \(category.watchingCount)")
                    .font(.caption).foregroundStyle(PW.muted)
            }
            Spacer(minLength: 0)
            VStack(alignment: .trailing, spacing: 5) {
                Text(category.totalText).font(.subheadline.weight(.semibold)).monospacedDigit()
                if let change = category.change {
                    PriceDelta(change: change)
                } else if !category.sparkline.isEmpty {
                    PriceChart(points: category.sparkline, compact: true).frame(width: 54)
                }
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                .foregroundStyle(PW.muted).accessibilityHidden(true)
        }
        .card()
    }

    private func runCard(_ run: CheckRun) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            Label("Проверка цен", systemImage: "waveform.path")
                .font(.headline)
            if run.active {
                ProgressView(value: Double(run.done), total: Double(max(1, run.total)))
                    .accessibilityLabel("Выполнено \(run.done) из \(run.total)")
            }
            Text(runCaption(run)).font(.subheadline).foregroundStyle(PW.muted)
            if run.failed > 0 {
                Text("Ошибки · \(run.failed)").font(.caption).foregroundStyle(PW.warning)
            }
            if let runError { Text(runError).font(.subheadline).foregroundStyle(PW.warning) }
            if run.canStart {
                Button("Обновить цены", systemImage: "arrow.clockwise") {
                    Task { await updateRun(stop: nil) }
                }
                .buttonStyle(.bordered).disabled(runBusy)
            } else if run.canStop, let id = run.id {
                Button("Остановить проверку", systemImage: "stop.circle") {
                    Task { await updateRun(stop: id) }
                }
                .buttonStyle(.bordered).disabled(runBusy)
            }
        }
        .card()
    }

    private func runCaption(_ run: CheckRun) -> String {
        switch run.status {
        case "queued": "В очереди · \(run.total) заданий"
        case "running": "Проверено \(run.done) из \(run.total)"
        case "stopping": "Завершаются текущие задания"
        case "worker_offline": "Парсер сейчас недоступен"
        case "failed": "Проверка завершилась с ошибкой"
        case "finished":
            run.finishedAt.map { "Обновлено \(Prices.date($0, withTime: true))" } ?? "Проверка завершена"
        default: "Проверок пока не было"
        }
    }

    private func load() async {
        let requested = period
        do {
            let value = try await client.dashboard(period: requested)
            guard !Task.isCancelled, period == requested else { return }
            dashboard = value
            if !runBusy { run = value.run }
            error = nil
        } catch {
            guard !Task.isCancelled, period == requested else { return }
            if !session.checkSession(error) { self.error = error.localizedDescription }
        }
    }

    private func trackRun() async {
        while run?.active == true, !Task.isCancelled {
            do {
                try await Task.sleep(for: .seconds(5))
                let value = try await client.runStatus()
                if Task.isCancelled || runBusy { continue }
                if value.active {
                    run = value
                } else {
                    await load()
                    if !Task.isCancelled, !runBusy { run = value }
                    return
                }
            } catch {
                guard !Task.isCancelled else { return }
                if session.checkSession(error) { return }
                runError = error.localizedDescription
            }
        }
    }

    private func updateRun(stop: Int?) async {
        guard !runBusy else { return }
        runBusy = true
        defer { runBusy = false }
        if pendingRun?.stopID != stop || pendingRun == nil { pendingRun = PendingRun(stopID: stop, key: UUID()) }
        guard let key = pendingRun?.key else { return }
        do {
            run = try await client.changeRun(stop: stop, key: key)
            pendingRun = nil
            runError = nil
        } catch {
            if case APIError.http = error { pendingRun = nil }
            if !session.checkSession(error) { runError = error.localizedDescription }
        }
    }
}

struct CategoryView: View {
    let id: Int
    let client: APIClient
    @Environment(SessionModel.self) private var session
    @Environment(WorkspaceNavigation.self) private var workspace
    @State private var period: Period
    @State private var range: ChartRange = .month
    @State private var category: CategoryDetail?
    @State private var points: [PricePoint]?
    @State private var error: String?
    @State private var chartError: String?

    init(id: Int, initialPeriod: Period, client: APIClient) {
        self.id = id
        self.client = client
        _period = State(initialValue: initialPeriod)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                SelectionStrip(selection: $period, title: \.title)
                if let error { LoadFailure(message: error) { Task { await load() } } }
                if let category, category.period == period {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Итог категории").font(.subheadline).foregroundStyle(PW.muted)
                        Text(category.totalText).font(.system(.largeTitle, design: .rounded, weight: .semibold)).monospacedDigit()
                        if let change = category.change { PriceDelta(change: change) }
                    }
                    .card()

                    VStack(alignment: .leading, spacing: 12) {
                        SectionTitle("История стоимости")
                        SelectionStrip(selection: $range, title: \.title)
                        if let chartError {
                            LoadFailure(message: chartError) { Task { await loadChart() } }
                        } else if let points {
                            PriceChart(points: points).id(range)
                        } else {
                            ProgressView().frame(maxWidth: .infinity).padding()
                        }
                    }
                    .card()

                    ForEach(category.groups.filter { !$0.items.isEmpty }) { group in
                        VStack(alignment: .leading, spacing: 9) {
                            SectionTitle(group.name ?? "Товары")
                            ForEach(group.items) { item in
                                NavigationLink { ProductView(id: item.id, client: client) } label: {
                                    productCard(item)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }

                    if let purchases = category.purchased, !purchases.isEmpty {
                        VStack(alignment: .leading, spacing: 9) {
                            SectionTitle("Куплено")
                            ForEach(purchases) { purchase in
                                NavigationLink { ProductView(id: purchase.itemId, client: client) } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 5) {
                                            Text(purchase.name).font(.headline)
                                            Text(Prices.date(purchase.purchasedAt)).font(.caption).foregroundStyle(PW.muted)
                                        }
                                        Spacer()
                                        Text(purchase.totalText).font(.subheadline.weight(.semibold))
                                    }
                                    .card()
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    if category.groups.allSatisfy({ $0.items.isEmpty }), category.purchased?.isEmpty != false {
                        ContentUnavailableView("Здесь пока нет товаров", systemImage: "basket").card()
                    }
                } else if error == nil {
                    ProgressView("Загружаем категорию").frame(maxWidth: .infinity).padding()
                }
            }
            .page()
        }
        .background(PW.canvas)
        .navigationTitle(category?.name ?? "Категория")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Добавить товар в категорию", systemImage: "plus") {
                    workspace.open(.newItem(category: id))
                }
                .labelStyle(.iconOnly)
            }
        }
        .task(id: "\(period.rawValue)-\(workspace.revision)") { await load() }
        .task(id: "\(range.rawValue)-\(workspace.revision)") { await loadChart() }
        .refreshable {
            async let detail: Void = load()
            async let history: Void = loadChart()
            _ = await (detail, history)
        }
    }

    private func productCard(_ item: ProductRow) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 5) {
                Text(item.name).font(.headline)
                if let store = item.bestStore { Text(store.name).font(.caption).foregroundStyle(PW.muted) }
                if let status = Prices.status(item.priceStatus, underReview: item.underReview) {
                    Text(status).font(.caption.weight(.medium)).foregroundStyle(PW.warning)
                }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 5) {
                Text(item.unitPriceText ?? "—").font(.headline).monospacedDigit()
                if item.quantity > 1, let line = item.linePriceText {
                    Text("\(item.quantity) шт. · \(line)").font(.caption).foregroundStyle(PW.muted)
                }
                if let regular = item.regularPriceText {
                    Text("Без карты \(regular)").font(.caption).foregroundStyle(PW.muted)
                }
            }
        }
        .card()
    }

    private func load() async {
        let requested = period
        do {
            let value = try await client.category(id, period: requested)
            guard !Task.isCancelled, period == requested else { return }
            category = value
            error = nil
        } catch {
            guard !Task.isCancelled, period == requested else { return }
            if !session.checkSession(error) { self.error = error.localizedDescription }
        }
    }

    private func loadChart() async {
        let requested = range
        chartError = nil
        points = nil
        do {
            let response = try await client.categoryChart(id, range: requested)
            guard !Task.isCancelled, range == requested else { return }
            points = response.points
        } catch {
            guard !Task.isCancelled, range == requested else { return }
            if !session.checkSession(error) { chartError = error.localizedDescription }
        }
    }
}
