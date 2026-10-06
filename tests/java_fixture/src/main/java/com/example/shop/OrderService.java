package com.example.shop;

public class OrderService {
    private final Pricing pricing;

    public OrderService(Pricing pricing) { this.pricing = pricing; }

    public int place(int qty) {
        int n = pricing.line(qty);
        helper();
        return n;
    }

    private void helper() {}

    public void again() { this.place(1); }

    public void localReceiver() {
        Pricing p = new DefaultPricing();
        p.line(2);
    }
}
