import XCTest
@testable import Lib

class BaseCase: XCTestCase {
    func makeCart() -> Cart {
        Cart()
    }
}

final class CartXCTests: BaseCase {
    func testCheckoutSumsItems() {
        let cart = makeCart()
        cart.items = [1, 2, 3]
        XCTAssertEqual(cart.checkout(), 6)
    }

    func testFormat() throws {
        XCTAssertEqual(formatPrice(3), "$3")
    }

    // takes a parameter: not an XCTest case
    func testHelper(_ value: Int) -> Int {
        value
    }

    static func testFactory() -> Cart {
        Cart()
    }
}

// not an XCTestCase: its test* methods are not test cases
struct TestDataBuilder {
    func testCart() -> Cart {
        Cart()
    }
}
