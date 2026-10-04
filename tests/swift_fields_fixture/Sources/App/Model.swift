@propertyWrapper
public struct Clamped {
    private var v: Double
    public init(wrappedValue: Double) { v = min(max(wrappedValue, 0), 1) }
    public var wrappedValue: Double {
        get { v }
        set { v = min(max(newValue, 0), 1) }
    }
}

public final class Store {
    @Clamped public var level: Double = 0
    public var history: [Double] = []
    public let limit = 10
    public static let shared = Store()

    public init() {
        _level = Clamped(wrappedValue: 0.5)
    }

    public func save(_ v: Double) {
        level = v
        history.append(level)
        self.level += 0
    }
}

public final class Picker {
    let store: Store

    init(store: Store) {
        self.store = store
        print(store.level)
    }

    func reset() {
        let s = Store()
        s.level = 0
        print(s.limit)
    }
}

public enum Route { case settings, about }

struct About { let title = "about" }

public func screen(_ route: Route, editing: Bool) -> Any {
    switch route {
    case .settings:
        return Picker(store: Store())
    default:
        break
    }
    if editing { return About() } else { return Picker(store: Store()) }
}
