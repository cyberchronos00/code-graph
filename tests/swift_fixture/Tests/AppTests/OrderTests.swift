import XCTest
@testable import App

final class OrderTests: XCTestCase {
    func testReindex() async throws {
        _ = try await OrderService().reindex()
    }
}
