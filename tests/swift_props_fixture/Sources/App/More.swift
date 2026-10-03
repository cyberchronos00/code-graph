import Foundation

extension Int {
    var asPrice: String { formatPrice(self) }                // computed property in an extension of an SDK type
}

final class Store {
    static var current: Store { Store() }                    // static computed property
    lazy var formatter: String = { formatPrice(1) }()        // lazy property with an initializer
    var name = "shop"                                        // stored: no node
    var total: Int = 0 {
        willSet { audit(newValue) }
        didSet { notify() }
    }
    var banner: String { "\(name) \(total.asPrice)" }

    func audit(_ v: Int) {}
    func notify() {}

    func checkout(_ cart: Cart) {
        cart.subtitle = "paid"                                // setter of a computed property
        cart.items = [1]                                      // write of an observed property
        total += 1                                            // write of an observed property (self)
        print(total)                                          // read of an observed property: runs no code of it
        print(formatter, Store.current.banner)
    }

    func shadowed(banner: String) -> String {
        let formatter = "local"
        return banner + formatter                             // parameter and local of the property's name
    }

    func label(for s: String) -> String { s }                 // a method with a property's name in another type
}
