import Testing
@testable import App

@Test func reloadsStores() {
    let store = harness.makeStore()
    store.reload(force: false)
}
