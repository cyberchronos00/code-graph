import XCTest
@testable import App

final class CartTests: XCTestCase {
    func testLabel() { XCTAssertEqual(Cart().label, "0.0") }
}
