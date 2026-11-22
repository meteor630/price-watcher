import Foundation
import XCTest
@testable import PriceWatcher

final class ContractTests: XCTestCase {
    func testDashboardDecodesServerMoneyAndNullableHistory() throws {
        let response = """
        {
          "as_of":"2026-11-22","period":"day","total_minor":1299000,"total_text":"12 990 ₽",
          "change":null,"in_total_count":1,"watching_count":1,
          "categories":[{"id":41,"name":"Техника","total_text":"12 990 ₽","change":null,"watching_count":1,
            "sparkline":[{"date":"2026-11-21","value_minor":null,"label":"Данных нет","status":"no_data"},
                         {"date":"2026-11-22","value_minor":1299000,"label":"22 ноя · 12 990 ₽","status":"ok"}]}],
          "panels":{"tracked":1,"cheaper":0,"pricier":0,"out_of_stock":0},
          "run":{"id":null,"status":"idle","total":0,"done":0,"failed":0,"finished_at":null,"can_start":true,"can_stop":false},
          "future_field":{"anything":true}
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let dashboard = try decoder.decode(Dashboard.self, from: Data(response.utf8))
        XCTAssertEqual(dashboard.totalText, "12 990 ₽")
        XCTAssertEqual(dashboard.categories.first?.sparkline.last?.valueMinor, 1299000)
        XCTAssertNil(dashboard.categories.first?.sparkline.first?.valueMinor)
        XCTAssertFalse(dashboard.run.active)
    }

    func testServerAddressAcceptsLANAndNormalizesOrigin() throws {
        let server = try ServerAddress(" HTTP://192.168.0.42:8000/api/v1/ ")
        XCTAssertEqual(server.url.absoluteString, "http://192.168.0.42:8000")
        XCTAssertEqual(server.profile.path(), "/web/settings/profile")
        XCTAssertNotNil(server.media("/media/product.webp"))
        XCTAssertNil(server.media("https://elsewhere.example/media/product.webp"))
        XCTAssertNil(server.media("/media/%2e%2e/anything"))
        for url in ["http://localhost:8000", "http://pricewatch.local:8000", "https://watch.example"] {
            XCTAssertNoThrow(try ServerAddress(url))
        }
    }

    func testServerAddressRejectsUnsafeOrAmbiguousAddresses() {
        for url in ["http://watch.example", "http://172.32.0.1", "http://0177.0.0.1",
                    "https://user:pass@watch.example", "https://watch.example/any-path", "https://watch.example?token=x"] {
            XCTAssertThrowsError(try ServerAddress(url), url)
        }
    }

    func testKeychainStoresIndependentRefreshTokensAndUpdatesInPlace() throws {
        let first = UUID().uuidString
        let second = UUID().uuidString
        defer {
            XCTAssertNoThrow(try Keychain.remove(first))
            XCTAssertNoThrow(try Keychain.remove(second))
        }
        XCTAssertNil(try Keychain.read(first))
        try Keychain.write("test-only-old", account: first)
        try Keychain.write("test-only-new", account: first)
        XCTAssertEqual(try Keychain.read(first), "test-only-new")
        XCTAssertNil(try Keychain.read(second))
        try Keychain.remove(first)
        XCTAssertNil(try Keychain.read(first))
    }
}
