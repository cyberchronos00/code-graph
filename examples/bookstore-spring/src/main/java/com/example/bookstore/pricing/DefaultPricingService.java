package com.example.bookstore.pricing;

import com.example.bookstore.order.Order;
import org.springframework.stereotype.Service;

@Service
public class DefaultPricingService implements PricingService {
    @Override
    public int lineTotal(Order order) {
        return order.getQty() * 10;
    }
}
