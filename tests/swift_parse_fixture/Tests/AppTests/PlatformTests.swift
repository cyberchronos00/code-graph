import Testing
@testable import App

struct PlatformTests {
#if os(macOS)
    @Test
#endif
    func loadsOnMac() {
        #expect(Store().load() == 1)
    }

    @Test func loads() {
        #expect(Store().load() == 1)
    }
}
