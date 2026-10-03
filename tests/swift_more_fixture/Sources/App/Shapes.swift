import Foundation

typealias Handler = () -> Void

extension String {
    init(order: Int) {
        self = "order-\(order)"
    }
}

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}

struct Box {
    init(width: Int, height: Int = 1) {}
    init(_ side: Int) {}
}

final class Retrier {
    init(_ handler: @escaping Handler) {}
}

final class Queue {
    func first(where predicate: (Int) -> Bool) -> Int? { nil }
}

func build() {
    let sdk = String(decoding: Data(), as: UTF8.self)
    let mine = String(order: 3)
    let raw = Data(base64Encoded: "")
    let wide = Box(width: 2)
    let square = Box(3)
    let odd = Box(depth: 1)
    let retrier = Retrier { }
    let firstBig = [1, 2].first(where: { $0 > 1 })
    print(sdk, mine, raw as Any, wide, square, odd, retrier, firstBig as Any)
}
