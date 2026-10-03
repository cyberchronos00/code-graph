import Styleguide
import Testing
@testable import App

@Test func matchesPositive() {
    #expect(Preview.matches(1, 1))
}

@Test func matchesNegative() {
    #expect(!Preview.matches(1, 2))
}

@Test func weights() {
    #expect(Weights.weight(from: 0, to: 2) == 2)
    #expect(Weights().weight(forExtraIndex: 1) == 1)
}

@Test func fontsRegistered() throws {
    try #require(!Styleguide.registerFonts() == false)
}
