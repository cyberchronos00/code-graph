import SwiftUI

struct Preview {
    static func matches(_ a: Int, _ b: Int) -> Bool { a == b }
}

enum Chrome {
    static func shouldAutoPresent() -> Bool { true }
}

final class SlotGate<Content> {
    func cancel() {}
    func requestFull() {}
}

final class Upload {
    func cancel() {}
}

final class Overlay {
    func show() {}
}

final class SomeClient {
    func close() {}
}

extension CGImage {
    func pdfData() -> Data { Data() }
}

struct Weights {
    func weight(forExtraIndex i: Int) -> Double { Double(i) }
    static func weight(from a: Int, to b: Int) -> Double { Double(b - a) }
    func total() -> Double {
        Self.weight(from: 0, to: 1) + weight(forExtraIndex: 2)
    }
    static func span() -> Double { weight(from: 1, to: 3) }
}

enum Scorer {
    static func score(_ x: Int) -> Int { x }
}

#if os(macOS)
final class HUDController {
    func toggle() {}
}
#endif
