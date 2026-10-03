protocol Shape {
    func area() -> Double
}

struct Circle: Shape {
    let r: Double
    func area() -> Double { 3.14 * r * r }
}

struct Square: Shape {
    let side: Double
    func area() -> Double { side * side }
    func describe() -> String { "square \(side)" }
}

final class Registry {
    var shapes: [Shape] = []

    func add(_ shape: Shape) {
        shapes.append(shape)
    }

    func add(side: Double) {
        add(Square(side: side))
    }

    func total() -> Double {
        shapes.map { $0.area() }.reduce(0, +)
    }
}
