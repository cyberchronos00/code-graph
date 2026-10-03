package demo;

public class Formatter {
    public static String bold(String s) {
        return "*" + s + "*";
    }

    public String area(Shape shape) {
        return bold(String.valueOf(shape.area()));
    }

    public Registry fresh() {
        Registry r = new Registry();
        r.add(new Circle(2.0));
        return r;
    }

    public String built() {
        return String.valueOf(AppKt.build());
    }

    static class Box {
        Formatter make() { return new Formatter(); }
    }
}
