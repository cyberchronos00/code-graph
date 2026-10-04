package app

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class Editor(private val scope: CoroutineScope) {
    var text = ""
    private var isApplyingRemote = false

    fun onTextChanged() {
        if (isApplyingRemote) return
        upload(text)
    }

    fun applyRemote(value: String) {
        isApplyingRemote = true
        text = value
        isApplyingRemote = false
    }

    fun refresh() {
        scope.launch {
            val value = withContext(Dispatchers.IO) { fetchText() }
            text = value
        }
    }

    private fun upload(value: String) {}
    private suspend fun fetchText(): String = ""
}
