package com.example.bookstore.order;

import com.example.bookstore.pricing.PricingService;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final PricingService pricing;
    private final OrderRepository orders;

    public OrderService(PricingService pricing, OrderRepository orders) {
        this.pricing = pricing;
        this.orders = orders;
    }

    public Order place(Order order) {
        pricing.lineTotal(order);
        return orders.save(order);
    }

    public Order find(Long id) {
        return orders.findById(id);
    }
}
