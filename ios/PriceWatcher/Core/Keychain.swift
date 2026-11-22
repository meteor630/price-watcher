import Foundation
import Security

enum Keychain {
    private static let service = "io.github.meteor630.pricewatcher.session"

    static func read(_ account: String) throws -> String? {
        var query = attributes(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let token = String(data: data, encoding: .utf8)
        else { throw APIError.keychain }
        return token
    }

    static func write(_ token: String, account: String) throws {
        let query = attributes(account)
        let value = [kSecValueData as String: Data(token.utf8)]
        let status = SecItemUpdate(query as CFDictionary, value as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw APIError.keychain }
        var item = query.merging(value) { _, value in value }
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw APIError.keychain }
    }

    static func remove(_ account: String) throws {
        let status = SecItemDelete(attributes(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw APIError.keychain }
    }

    private static func attributes(_ account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }
}
