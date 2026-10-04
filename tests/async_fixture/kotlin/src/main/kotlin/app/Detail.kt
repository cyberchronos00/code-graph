package app

class DetailViewModel(private val scope: Scope, seed: String) {
    var title: String = ""

    init {
        title = seed.trim()
    }

    fun refresh(q: String) {
        scope.launch {
            val t = withContext(io) { q.trim() }
            title = t
        }
    }
}
