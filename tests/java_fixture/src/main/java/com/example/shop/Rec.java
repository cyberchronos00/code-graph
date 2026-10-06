package com.example.shop;

public record Rec(String name, int n) implements Marker {
    public int len() { return name.length(); }
}
