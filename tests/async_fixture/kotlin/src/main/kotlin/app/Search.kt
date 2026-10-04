package app

class SearchViewModel(private val scope: Scope) {
    var results: List<String> = emptyList()
    var requestId = 0

    fun search(q: String) {
        scope.launch {
            val found = withContext(io) { fetch(q) }
            results = found
        }
    }

    fun searchGuarded(q: String) {
        val id = ++requestId
        scope.launch {
            val found = withContext(io) { fetch(q) }
            if (id == requestId) {
                results = found
            }
        }
    }

    fun fetch(q: String): List<String> = listOf(q)
}

class Scope { fun launch(f: () -> Unit) = f() }
val io = 0
fun <T> withContext(c: Int, f: () -> T): T = f()
