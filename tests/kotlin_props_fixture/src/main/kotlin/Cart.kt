package app

import kotlin.properties.Delegates

fun formatPrice(c: Int): String = "${c / 100}"

class Cart {
    var items: List<Int> = emptyList()
        set(value) { field = value; log(formatPrice(value.size)) }
    val label: String get() = formatPrice(items.sum())
    val cached: String by lazy { formatPrice(1) }
    var count: Int by Delegates.observable(0) { _, _, n -> log(formatPrice(n)) }
    fun log(s: String) { println(s) }
}

val Int.asPrice: String get() = formatPrice(this)
val banner: String get() = formatPrice(0)

class Checkout(val cart: Cart) {
    val summary: String get() = cart.label
    fun render(): String = summary + banner + 3.asPrice
    fun reset() { cart.items = emptyList() }
}
