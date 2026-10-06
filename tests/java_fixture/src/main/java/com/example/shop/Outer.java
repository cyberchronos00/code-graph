package com.example.shop;

public class Outer {
    public class Inner {
        public int value() { return 1; }
    }

    public int useInner() {
        return new Inner().value();
    }

    public void anon() {
        Runnable r = new Runnable() {
            public void run() { ping(); }
        };
        r.run();
    }

    public void ping() {}

    public void lambda() {
        Runnable r = () -> ping();
        r.run();
    }

    public void ref() {
        Runnable r = this::ping;
    }
}
