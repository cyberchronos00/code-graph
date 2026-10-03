package shop

import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import kotlin.test.assertEquals

class CalcTest {
    private fun calc() = Calc()

    @Test
    fun totalOfEmptyIsZero() {
        assertEquals(0, calc().total(emptyList()))
    }

    @ParameterizedTest
    @ValueSource(ints = [0, 10, 50])
    fun discountNeverGrows(percent: Int) {
        assert(Calc().discount(100, percent) <= 100)
    }

    // a helper without @Test is not a test case
    fun makeItems(): List<Int> = listOf(1, 2)
}
