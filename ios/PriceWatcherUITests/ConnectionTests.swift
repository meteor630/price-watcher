import XCTest

final class ConnectionTests: XCTestCase {
    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    @MainActor
    func testConnectionInstructionsAndInvalidCode() {
        let app = XCUIApplication()
        app.launch()
        let code = app.textFields["pair-code"]
        XCTAssertTrue(code.waitForExistence(timeout: 8))
        XCTAssertTrue(app.textFields["Адрес сервера"].exists)
        let connect = app.buttons["connect-submit"]
        XCTAssertFalse(connect.isEnabled)
        code.tap()
        code.typeText("1234")
        XCTAssertFalse(connect.isEnabled)
    }

    @MainActor
    func testDockerPairingAndDashboard() throws {
        guard let testCode = ProcessInfo.processInfo.environment["PRICEWATCH_TEST_CODE"] else {
            throw XCTSkip("Для сквозной проверки нужен свежий код тестовой учётной записи Docker.")
        }
        let app = XCUIApplication()
        app.launch()
        let code = app.textFields["pair-code"]
        XCTAssertTrue(code.waitForExistence(timeout: 8))
        if let server = ProcessInfo.processInfo.environment["PRICEWATCH_TEST_SERVER"] {
            let address = app.textFields["Адрес сервера"]
            if address.value as? String != server {
                let clear = app.buttons["clear-server"]
                if clear.exists { clear.tap() }
                address.tap()
                address.typeText(server)
            }
        }
        code.tap()
        code.typeText(testCode)
        app.buttons["connect-submit"].tap()
        XCTAssertTrue(app.staticTexts["dashboard-total"].waitForExistence(timeout: 20))
        capture(app, name: "PriceWatcher-Home")
        if let category = ProcessInfo.processInfo.environment["PRICEWATCH_TEST_CATEGORY"] {
            app.staticTexts[category].tap()
            XCTAssertTrue(app.staticTexts["История стоимости"].waitForExistence(timeout: 8))
            let chart = app.descendants(matching: .any)["native-price-chart"].firstMatch
            if chart.waitForExistence(timeout: 5) {
                chart.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.5)).tap()
                capture(app, name: "PriceWatcher-Native-Chart")
            }
            if let product = ProcessInfo.processInfo.environment["PRICEWATCH_TEST_PRODUCT"] {
                app.staticTexts[product].firstMatch.tap()
                XCTAssertTrue(app.staticTexts["Динамика цены"].waitForExistence(timeout: 8))
                capture(app, name: "PriceWatcher-Product")
                let total = app.switches["Учитывать в итоге"]
                if !total.isHittable { app.swipeUp() }
                XCTAssertTrue(total.isHittable)
                total.tap()
                app.buttons["product-save"].tap()
                let noPendingEdits = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"),
                                                              object: app.buttons["product-save"])
                XCTAssertEqual(XCTWaiter.wait(for: [noPendingEdits], timeout: 8), .completed)
            }
            app.navigationBars.buttons.element(boundBy: 0).tap()
            if !app.buttons["Профиль"].exists { app.navigationBars.buttons.element(boundBy: 0).tap() }
        }
        if ProcessInfo.processInfo.environment["PRICEWATCH_TEST_MANAGEMENT"] == "1" {
            manageNativeProduct(app)
        }
        app.buttons["Профиль"].tap()
        app.buttons["Выйти"].tap()
        XCTAssertTrue(app.textFields["pair-code"].waitForExistence(timeout: 8))
    }

    @MainActor
    private func manageNativeProduct(_ app: XCUIApplication) {
        XCTAssertFalse(app.webViews.firstMatch.exists)
        app.buttons["overview-add-item"].tap()
        let name = "iPhone " + UUID().uuidString.prefix(6)
        let field = app.textFields["editor-name"]
        XCTAssertTrue(field.waitForExistence(timeout: 15))
        capture(app, name: "PriceWatcher-Native-Editor")
        field.tap()
        field.typeText(name)
        app.buttons["editor-save"].tap()
        let editorClosed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [editorClosed], timeout: 15), .completed)
        app.tabBars.buttons["Управление"].tap()
        XCTAssertTrue(app.staticTexts["Товары"].waitForExistence(timeout: 10))
        capture(app, name: "PriceWatcher-Native-Management")
        app.staticTexts["Товары"].tap()
        let added = app.staticTexts[name]
        XCTAssertTrue(added.waitForExistence(timeout: 12))
        added.tap()
        let edit = app.buttons["Действия с товаром"]
        XCTAssertTrue(edit.waitForExistence(timeout: 10))
        edit.tap()
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        XCTAssertEqual(field.value as? String, name)
        let remove = app.buttons["editor-delete"]
        for _ in 0..<3 {
            if remove.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(remove.isHittable)
        remove.tap()
        let confirm = app.buttons.matching(NSPredicate(format: "label == %@ AND identifier != %@", "Удалить товар", "editor-delete")).firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 8))
        confirm.tap()
        XCTAssertTrue(app.staticTexts["Товар удалён"].waitForExistence(timeout: 10))
        app.buttons["Закрыть"].tap()
        app.tabBars.buttons["Обзор"].tap()
        XCTAssertTrue(app.buttons["Профиль"].waitForExistence(timeout: 8))
    }

    @MainActor
    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
