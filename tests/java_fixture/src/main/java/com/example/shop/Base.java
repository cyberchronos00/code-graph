package com.example.shop;

public class Base {
    public void open() {}
}

class Child extends Base {
    @Override
    public void open() { super.open(); }
}
