package demo

class OrderEvents(private val orders: OrderService) {
    fun onPlaced(id: Long) {
        orders.find(id)
    }
}
