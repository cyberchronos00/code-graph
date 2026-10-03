package shop

import org.junit.*

class LegacyCalcTest {
    @Test
    fun sumsItems() {
        Assert.assertEquals(3, Calc().total(listOf(1, 2)))
    }
}
