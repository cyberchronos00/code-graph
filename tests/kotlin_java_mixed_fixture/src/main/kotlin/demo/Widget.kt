@file:JvmName("WidgetKit")
package demo

fun top(): Int = 1

class Widget {
    companion object {
        fun make(): Widget = Widget()

        @JvmStatic
        fun stat(): Int = 2
    }

    val name: String = "n"
    var count: Int = 0
    val ready: Boolean = true
}
