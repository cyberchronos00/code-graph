import Testing
@testable import App

struct ExpectTests {
    func expectLoaded(_ store: Store, sourceLocation: SourceLocation = #_sourceLocation) {
        #expect(store.load() == 1, sourceLocation: sourceLocation)
    }
    @Test func loads() { expectLoaded(Store()) }         // missing from the graph
    @Test func loadsAgain() { expectLoaded(Store()) }
}
