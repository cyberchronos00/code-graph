package demo

fun build(): Registry {
    val registry = Registry()
    registry.add(Circle(1.0))
    registry.add(2.0)
    return registry
}

fun main() {
    val report = Report(Store())
    println(report.render(build()))
}
