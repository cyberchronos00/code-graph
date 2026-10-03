// Valid Swift the tree-sitter grammar does not parse as written (#73); every declaration here must be indexed.
enum Outcome {
    case done(Void)
    case none
}

typealias Client = Loader
    & Saver

protocol Loader {}
protocol Saver {}

struct Runner {
    func first() -> Outcome { .done(()) }
    func second(_ info: [String: Any]) -> String { info["name"] as? String ?? "" }
    func third() async throws -> Int {
        if let n = try? await fetch() {
            return n
        }
        return 0
    }
    func fetch() async throws -> Int { 3 }
    func total(_ base: Int, _ word: String) -> Int {
        let t = base
            * word.count
        return t
    }
    func callback() -> (@convention(c) (Int32) -> Void)? { nil }
    func check(_ o: Outcome) -> Bool {
        switch o {
        case .done():
            return true
        default:
            return false
        }
    }
}
