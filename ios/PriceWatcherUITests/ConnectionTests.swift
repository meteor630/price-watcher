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
        app.buttons["Профиль"].tap()
        app.buttons["Выйти"].tap()
        XCTAssertTrue(app.textFields["pair-code"].waitForExistence(timeout: 8))
    }

    @MainActor
    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
