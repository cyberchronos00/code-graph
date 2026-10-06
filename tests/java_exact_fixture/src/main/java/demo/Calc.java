package demo;

public class Calc {
    public int value;

    public Calc(int value) {
        this.value = value;
    }

    public int add(int x) {
        return value + x;
    }

    public int add(int x, int y) {
        return add(x) + y;
    }

    public static class Inner {
        public int bump(Calc c) {
            return c.add(1);
        }
    }
}
