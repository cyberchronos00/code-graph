import Testing
import Lib

// an Xcode test target folder that does not end in Tests: the Testing import marks it as test code
struct AppChecks {
    @Test func cartStartsEmpty() {
        #expect(Cart().checkout() == 0)
    }
}
