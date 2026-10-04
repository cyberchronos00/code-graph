package app

import kotlin.test.Test
import kotlin.test.assertEquals

class CartTest {
    @Test fun label() { assertEquals("0", Cart().label) }
}
