package demo

interface Shape {
    fun area(): Double
}

class Circle(val r: Double) : Shape {
    override fun area(): Double = 3.14 * r * r
}

class Square(val side: Double) : Shape {
    override fun area(): Double = side * side
    fun describe(): String = "square $side"
}

class Registry {
    var shapes: MutableSet<Shape> = linkedSetOf()

    fun getShapes(): List<Shape> = shapes.sortedBy { it.area() }

    fun add(shape: Shape) {
        shapes.add(shape)
    }

    fun add(side: Double) {
        add(Square(side))
    }
}
