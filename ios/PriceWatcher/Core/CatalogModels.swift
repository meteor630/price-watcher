import Foundation

struct NamedID: Decodable, Identifiable, Sendable {
    let id: Int
    let name: String
}

struct CatalogCategories: Decodable, Sendable {
    let orderVersion: Int
    let categories: [CatalogCategory]
}

struct CatalogCategory: Decodable, Identifiable, Sendable {
    let categoryId: Int
    let name: String
    let version: Int
    let itemCount: Int
    let totalText: String
    let groups: [CatalogGroup]

    var id: Int { categoryId }
}

struct CatalogGroup: Decodable, Identifiable, Sendable {
    let groupId: Int
    let name: String
    let version: Int
    let itemCount: Int

    var id: Int { groupId }
}

struct CatalogEntry: Decodable, Identifiable, Sendable {
    let product: ProductRow
    let categoryId: Int
    let categoryName: String
    let groupId: Int?
    let groupName: String?
    let siblings: [Int]
    let parentVersion: Int

    var id: Int { product.id }
}

struct CatalogLink: Decodable, Identifiable, Sendable {
    let itemLinkId: Int
    let url: String
    let storeName: String
    let status: String
    let priceText: String?

    var id: Int { itemLinkId }
}

struct LinkResult: Decodable, Sendable {
    let url: String
    let error: String?
}

struct Mutation: Decodable, Sendable {
    let id: Int?
    let undoToken: String?
    let results: [LinkResult]?
}

struct CollectionPage<Element: Decodable & Sendable>: Decodable, Sendable {
    let items: [Element]
    let nextCursor: String?
}

struct ItemDraft: Encodable, Sendable {
    var name = ""
    var fullName = ""
    var categoryId: Int
    var groupId: Int?
    var quantity = 1
    var version: Int?
    var links: [String]?
}

struct Reorder: Encodable, Sendable {
    let ids: [Int]
    let version: Int
    var categoryId: Int?
    var groupId: Int?
}

struct PurchaseDefaults: Decodable, Sendable {
    let version: Int
    let name: String
    let priceInput: String
    let stores: [Store]
    let storeId: Int?
    let quantity: Int
    let today: String
}

struct PurchaseEntry: Decodable, Identifiable, Sendable {
    let purchase: Purchase
    let category: NamedID
    let savings: Savings?
    var id: Int { purchase.id }
}

struct Savings: Decodable, Sendable {
    let savingsMinor: Int
    let savingsText: String
}

struct CategorySpending: Decodable, Identifiable, Sendable {
    let category: NamedID
    let count: Int
    let spentText: String
    let savings: Savings?
    var id: Int { category.id }
}

struct PurchasePage: Decodable, Sendable {
    let spentText: String
    let count: Int
    let savings: Savings?
    let categories: [CategorySpending]
    let items: [PurchaseEntry]
    let nextCursor: String?
}

struct ArchiveEntry: Decodable, Identifiable, Sendable {
    let item: ProductRow
    let category: NamedID
    let archivedAt: String
    var id: Int { item.id }
}

struct Notice: Decodable, Identifiable, Sendable {
    let id: Int
    let text: String
    let createdAt: String
    let read: Bool
}

struct ReviewEntry: Decodable, Identifiable, Sendable {
    let id: Int
    let itemId: Int
    let itemName: String
    let store: Store
    let url: String
    let kind: String
    let beforeText: String
    let afterText: String
}

struct DashboardEntry: Decodable, Identifiable, Sendable {
    let itemId: Int
    let name: String
    let category: NamedID?
    let oldPriceText: String?
    let newPriceText: String?
    var id: Int { itemId }
}

struct EmptyPayload: Encodable, Sendable {}
struct VersionPayload: Encodable, Sendable { let version: Int }
