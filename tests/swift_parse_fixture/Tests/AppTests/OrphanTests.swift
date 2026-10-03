import XCTest
@testable import App

final class OrphanTests: XCTestCase {
#warningx("x")
    func testBeforeMacro() { XCTAssertEqual(Store().load(), 1) }
#warningx()
    func testAfterMacro() { XCTAssertEqual(Store().load(), 1) }    
}
