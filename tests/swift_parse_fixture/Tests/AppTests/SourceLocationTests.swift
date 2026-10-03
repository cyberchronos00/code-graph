import XCTest
@testable import App

final class SourceLocationTests: XCTestCase {
#sourceLocation(file: "Generated.swift", line: 1)
    func testGenerated() { XCTAssertEqual(Store().load(), 1) }   // becomes function:testGenerated (not a test)
#sourceLocation()
    func testAfter() { XCTAssertEqual(Store().load(), 1) }       // becomes function:testAfter
}
