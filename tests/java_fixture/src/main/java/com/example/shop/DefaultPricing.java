package com.example.shop;

public class DefaultPricing implements Pricing {
    public int line(int qty) { return qty; }
    public int line(int qty, int tax) { return qty + tax; }
}
