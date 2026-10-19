package booth

class Cart {
    val label: String
        get() = "L"

    var items: Int = 1

    fun read(): String = label

    fun bump() { items += 1 }

    fun replace() { items = 2 }

    fun getItems(): Int = items
}

fun show(c: Cart): String = c.label

fun plain(c: Cart): Int = c.getItems()
