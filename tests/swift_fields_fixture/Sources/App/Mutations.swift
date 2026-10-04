import SwiftUI

struct Basket {
    var items: [String] = []
    var open = false
    var tags: Set<String> = []

    mutating func add(_ s: String) {
        items.append(s)
        self.tags.insert(s)
        open.toggle()
        bump(&items)
        let n = items.count
        _ = n
    }

    func bump(_ xs: inout [String]) {}
}

struct BasketView: View {
    @State private var basket = Basket()
    @AppStorage("compact") private var compact = false
    @Binding var shown: Bool

    var body: some View {
        Toggle("Compact", isOn: $compact)
        Toggle("Shown", isOn: $shown)
        Text(compact ? "c" : "w")
            .onTapGesture { basket.add("x") }
        List(basket.items, id: \.self) { Text($0) }
        Text("").sorted(by: \Basket.items)
    }
}

extension Text {
    func sorted<T>(by k: KeyPath<Basket, T>) -> Text { self }
}
