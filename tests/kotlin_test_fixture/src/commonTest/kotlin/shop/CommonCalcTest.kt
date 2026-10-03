package shop

import kotlin.test.Test
import kotlin.test.assertEquals

class CommonCalcTest {
    @Test
    fun discountOfZero() {
        assertEquals(0, Calc().discount(0, 10))
    }
}
