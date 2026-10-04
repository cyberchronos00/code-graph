package app

annotation class Composable

class Banner(val text: String)

@Composable
fun Spinner() {}

@Composable
fun Feed(items: List<String>) {}

@Composable
fun Home(loading: Boolean, tab: Int) {
    if (loading) {
        Spinner()
    } else {
        Feed(listOf("a"))
    }
    when (tab) {
        1 -> Banner("one")
        else -> Feed(emptyList())
    }
    Spinner()
}
