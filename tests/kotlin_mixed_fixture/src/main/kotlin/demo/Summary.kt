package demo

fun summary(): String {
    val f = Formatter()
    return Formatter.bold(f.area(Circle(3.0))) + f.fresh().getShapes().size
}
