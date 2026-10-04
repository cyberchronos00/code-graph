import XCTest
@testable import App

final class StoreTests: XCTestCase {
    func testSave() {
        let s = Store()
        s.save(2)
        XCTAssertEqual(s.level, 1)
    }
}
