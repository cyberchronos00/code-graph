import Testing
@testable import Lib

extension Tag {
    @Tag static var money: Self
}

@Suite("Discounts", .tags(.money))
struct DiscountTests {
    @Test("percent off", .tags(.money), arguments: [0, 10, 50])
    func percentOff(percent: Int) {
        #expect(Pricing().discount(100, percent: percent) <= 100)
    }

    @Suite("Edge cases") struct Edge {
        @Test(.disabled("flaky on CI"), .bug("https://example.com/issues/1"))
        func hundredPercent() {
            #expect(Pricing().discount(100, percent: 100) == 0)
        }
    }

    // a helper in a test file is not a test case
    func makePricing() -> Pricing {
        Pricing()
    }
}

extension DiscountTests {
    @Test func zeroAmount() {
        #expect(makePricing().discount(0, percent: 10) == 0)
    }
}
