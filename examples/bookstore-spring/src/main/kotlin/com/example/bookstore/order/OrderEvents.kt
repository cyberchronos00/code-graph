package com.example.bookstore.order

import org.springframework.context.event.EventListener

class OrderPlaced(val id: Long)

class OrderEvents(private val orders: OrderService) {
    @EventListener
    fun onPlaced(event: OrderPlaced) {
        orders.find(event.id)
    }
}
