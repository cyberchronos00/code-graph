package com.example.bookstore.web;

import com.example.bookstore.order.Order;
import com.example.bookstore.order.OrderService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    @PostMapping("/api/orders")
    public Order create(Order order) {
        return orders.place(order);
    }

    @GetMapping("/api/orders/{id}")
    public Order get(Long id) {
        return orders.find(id);
    }
}
