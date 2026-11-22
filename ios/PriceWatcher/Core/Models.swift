import Foundation

enum Period: String, CaseIterable, Identifiable, Decodable, Sendable {
    case day, week, month, year
    var id: Self { self }
    var title: String {
        switch self {
        case .day: "День"
        case .week: "Неделя"
        case .month: "Месяц"
        case .year: "Год"
        }
    }
}

enum ChartRange: String, CaseIterable, Identifiable {
    case week, month, year
    var id: Self { self }
    var title: String { Period(rawValue: rawValue)?.title ?? rawValue }
}

struct TokenPair: Decodable, Sendable {
    let accessToken: String
    let accessExpiresAt: String
    let refreshToken: String
}

struct Account: Decodable, Sendable {
    let displayName: String
    let role: String
}

struct PriceChange: Decodable, Sendable {
    let direction: String
    let changeText: String
    let percentText: String
}

struct Store: Decodable, Identifiable, Sendable {
    let id: Int
    let name: String
}

struct Dashboard: Decodable, Sendable {
    let asOf: String
    let period: Period
    let totalText: String
    let change: PriceChange?
    let inTotalCount: Int
    let watchingCount: Int
    let reviewsPending: Int?
    let categories: [CategorySummary]
    let panels: Panels
    let run: CheckRun

    struct Panels: Decodable, Sendable {
        let tracked: Int?
        let cheaper: Int
        let pricier: Int
        let outOfStock: Int
    }
}

struct CategorySummary: Decodable, Identifiable, Sendable {
    let id: Int
    let name: String
    let totalText: String
    let change: PriceChange?
    let watchingCount: Int
    let sparkline: [PricePoint]
}

struct CategoryDetail: Decodable, Sendable {
    let name: String
    let period: Period
    let totalText: String
    let change: PriceChange?
    let groups: [ProductGroup]
    let purchased: [Purchase]?
}

struct ProductGroup: Decodable, Identifiable, Sendable {
    let id: Int?
    let name: String?
    let items: [ProductRow]
}

struct ProductRow: Decodable, Identifiable, Sendable {
    let id: Int
    let version: Int?
    let name: String
    let quantity: Int
    let inTotal: Bool
    let priceStatus: String
    let underReview: Bool
    let bestStore: Store?
    let unitPriceText: String?
    let linePriceText: String?
    let regularPriceText: String?
}

struct ProductDetail: Decodable, Sendable {
    let id: Int
    let version: Int
    let name: String
    let fullName: String?
    let category: NamedID?
    let group: NamedID?
    let quantity: Int
    let inTotal: Bool
    let photoUrl: String?
    let priceStatus: String
    let underReview: Bool
    let bestStore: Store?
    let unitPriceText: String?
    let regularPriceText: String?
    let failureReason: String?
    let offers: [StoreOffer]
    let history: [HistoryEvent]
    let state: String?
    let purchase: Purchase?
}

struct StoreOffer: Decodable, Identifiable, Sendable {
    let store: Store
    let url: String
    let priceStatus: String
    let priceText: String?
    let cardPriceText: String?
    let checkedAt: String?
    let isBest: Bool

    var id: Int { store.id }
}

struct HistoryEvent: Decodable, Sendable {
    let at: String
    let store: Store
    let text: String
}

struct Purchase: Decodable, Identifiable, Sendable {
    let itemId: Int
    let name: String
    let purchasedAt: String
    let totalText: String
    let store: Store?

    var id: Int { itemId }
}

struct PricePoint: Decodable, Identifiable, Sendable {
    let date: String
    let valueMinor: Int?
    let label: String
    let status: String

    var id: String { date }
}

struct ChartResponse: Decodable, Sendable {
    let points: [PricePoint]
}

struct CheckRun: Decodable, Sendable {
    let id: Int?
    let status: String
    let total: Int
    let done: Int
    let failed: Int
    let finishedAt: String?
    let canStart: Bool
    let canStop: Bool

    var active: Bool { ["queued", "running", "stopping", "worker_offline"].contains(status) }
}

enum Prices {
    static func status(_ status: String, underReview: Bool = false) -> String? {
        if underReview { return "На проверке" }
        switch status {
        case "ok": return nil
        case "check_failed": return "Последняя известная цена"
        case "stale": return "Цена устарела"
        case "out_of_stock": return "Нет в наличии"
        case "awaiting_check": return "Ожидает проверки"
        case "store_disabled": return "Магазин отключён"
        case "no_links": return "Нет ссылок"
        default: return "Состояние цены неизвестно"
        }
    }

    static func date(_ value: String, withTime: Bool = false) -> String {
        let format = Date.ISO8601FormatStyle()
        let date = (try? format.parse(value))
            ?? (try? format.time(includingFractionalSeconds: true).parse(value))
            ?? (try? Date.ISO8601FormatStyle().year().month().day().dateSeparator(.dash).parse(value))
        guard let date else { return value }
        var display = Date.FormatStyle().day().month(.abbreviated).locale(Locale(identifier: "ru_RU"))
        if let zone = TimeZone(identifier: "Europe/Moscow") { display.timeZone = zone }
        if withTime { display = display.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits) }
        return date.formatted(display)
    }
}
