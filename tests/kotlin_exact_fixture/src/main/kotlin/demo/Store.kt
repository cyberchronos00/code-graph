package demo

class Cache {
    fun load(key: String): String = key
}

class Store {
    fun load(key: String): String = key.uppercase()
}

fun Shape.label(): String = "shape " + area()

class Report(private val store: Store) {
    fun render(registry: Registry): String {
        val first = registry.getShapes().first()
        return store.load(first.label())
    }
}
