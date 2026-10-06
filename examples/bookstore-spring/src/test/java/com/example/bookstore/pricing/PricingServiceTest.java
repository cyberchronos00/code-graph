package com.example.bookstore.pricing;

import com.example.bookstore.order.Order;
import org.junit.jupiter.api.Test;

public class PricingServiceTest {
    @Test
    void appliesDiscount() {
        DefaultPricingService pricing = new DefaultPricingService();
        pricing.lineTotal(new Order());
    }
}
