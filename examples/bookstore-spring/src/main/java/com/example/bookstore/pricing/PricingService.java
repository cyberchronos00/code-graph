package com.example.bookstore.pricing;

import com.example.bookstore.order.Order;

public interface PricingService {
    int lineTotal(Order order);
}
