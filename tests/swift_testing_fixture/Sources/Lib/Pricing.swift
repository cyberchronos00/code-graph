public struct Pricing {
    public init() {}

    public func total(_ items: [Int]) -> Int {
        items.reduce(0, +)
    }

    public func discount(_ amount: Int, percent: Int) -> Int {
        amount - amount * percent / 100
    }
}

public final class Cart {
    private let pricing = Pricing()
    public var items: [Int] = []

    public init() {}

    public func checkout() -> Int {
        pricing.total(items)
    }
}

public func formatPrice(_ value: Int) -> String {
    "$\(value)"
}
