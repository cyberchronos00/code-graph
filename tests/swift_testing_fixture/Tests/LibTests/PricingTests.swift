import Testing
@testable import Lib

@Suite struct PricingTests {
    @Test func totalOfEmptyIsZero() {
        #expect(Pricing().total([]) == 0)
    }

    @Test("sums items") func sums() {
        #expect(Pricing().total([1, 2]) == 3)
    }
}

@Test func freeFunctionTest() {
    #expect(Pricing().total([5]) == 5)
}
