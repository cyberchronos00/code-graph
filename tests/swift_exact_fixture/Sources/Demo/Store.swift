final class Cache {
    func load(_ key: String) -> String { key }
}

final class Store {
    func load(_ key: String) -> String { key.uppercased() }
}

extension Shape {
    func label() -> String { "shape \(area())" }
}

final class Report {
    private let store: Store
    init(store: Store) { self.store = store }

    func render(_ registry: Registry) -> String {
        let first = registry.shapes[0]
        return store.load(first.label())
    }
}
