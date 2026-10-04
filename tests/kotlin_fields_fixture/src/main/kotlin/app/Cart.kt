package app

class Cart(val owner: String, var total: Int, note: String) {
    var items: MutableList<String> = mutableListOf()
    private val tags = mutableSetOf<String>()
    var label: String
        get() = owner
        set(v) {}

    fun add(s: String) {
        items.add(s)
        this.tags.add(s)
        total += 1
        total = total + 1
        val n = items.size
    }
}

class Shop {
    fun reset(c: Cart) {
        c.total = 0
        println(c.owner)
        val d = Cart("x", 1, "")
        d.items.clear()
    }
}

object Config { val limit = 3 }

class Vm {
    private val _state = kotlinx.coroutines.flow.MutableStateFlow(0)
    fun bump() { _state.value = 1 }
}
