import Foundation

final class Region {
    var points: [Double] = []

    func contains(normalized x: Double, y: Double) -> Bool { x >= 0 && y >= 0 }
    func contains(_ point: Double) -> Bool { points.contains(point) }
}

final class Selection {
    var region: Region?
    var current: Region!

    func hit(_ other: Selection) -> Bool {
        if region?.contains(normalized: 0.1, y: 0.2) == true { return true }          // optional chain, typed
        if current!.contains(normalized: 0.3, y: 0.4) { return true }                // force unwrap, typed
        return other.region!.contains(normalized: 0.5, y: 0.5)                        // untyped receiver chain
    }

    func probe(_ items: [Double], lookup: [String: Region]) -> Bool {
        let tags = ["a", "b"]
        _ = tags.contains("a")                                  // Array.contains(_:): no edge
        _ = items.contains(where: { $0 > 1 })                   // Sequence.contains(where:): no edge
        return lookup["x"]!.contains(normalized: 0.5, y: 0.5)    // untyped, unique labelled selector: kept
    }
}
