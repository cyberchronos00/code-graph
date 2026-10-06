package com.example.other;

import com.example.shop.OrderService;
import com.example.shop.Pricing;

public class Explicit {
    public int go(Pricing pricing) {
        OrderService svc = new OrderService(pricing);
        return svc.place(3);
    }
}
