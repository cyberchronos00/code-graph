package shop

class Calc {
    fun total(items: List<Int>): Int = items.sum()

    fun discount(amount: Int, percent: Int): Int = amount - amount * percent / 100
}
