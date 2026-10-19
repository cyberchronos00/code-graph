package app

class LookupViewModel(private val scope: Scope, private val repo: Repo) {
    var matches: List<String> = emptyList()

    fun lookup(q: String) {
        scope.launch {
            val hits = repo.find(q)
            matches = hits
        }
    }

    fun lookupFixed() {
        scope.launch {
            val hits = repo.all()
            matches = hits
        }
    }

    fun lookupLocal(q: String) {
        scope.launch {
            val hits = repo.local(q)
            matches = hits
        }
    }
}

class Repo {
    suspend fun find(q: String): List<String> = listOf(q)
    suspend fun all(): List<String> = emptyList()
    fun local(q: String): List<String> = listOf(q)
}
