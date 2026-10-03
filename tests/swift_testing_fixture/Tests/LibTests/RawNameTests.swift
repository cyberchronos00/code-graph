import Testing
@testable import Lib

// Swift 6.2 raw identifiers as test and suite names
struct `Cart checkout tests` {
    @Test func `checkout sums the items`() {
        let cart = Cart()
        cart.items = [2, 3]
        #expect(cart.checkout() == 5)
    }

    @`Test`(.tags(.money)) func backtickedAttribute() {
        #expect(formatPrice(1) == "$1")
    }
}
