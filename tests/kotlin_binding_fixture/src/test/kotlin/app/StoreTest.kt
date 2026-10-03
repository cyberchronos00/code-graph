package app

import org.junit.jupiter.api.Test

class StoreTest {
    @Test
    fun reloadsStores() {
        val s = harness.makeStore()
        s.reload(false)
    }
}
