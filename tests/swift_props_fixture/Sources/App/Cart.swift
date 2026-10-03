func formatPrice(_ cents: Int) -> String { "\(cents / 100).\(cents % 100)" }

public final class Cart {
    var items: [Int] = [] {
        didSet { log(formatPrice(items.count)) }            // observer
    }
    var label: String { formatPrice(items.reduce(0, +)) }   // computed property
    var subtitle: String {
        get { formatPrice(items.first ?? 0) }
        set { log(newValue) }
    }
    public init() {}
    func log(_ s: String) { print(s) }
}

struct Checkout {
    let cart: Cart
    var summary: String { cart.label }                       // reads a computed property
    func render() -> String { summary }
}
