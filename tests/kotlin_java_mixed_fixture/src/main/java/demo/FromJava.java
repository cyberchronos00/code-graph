package demo;

public class FromJava {
    public int a() { return WidgetKit.top(); }

    public Widget b() { return Widget.Companion.make(); }

    public int c() { return Widget.stat(); }

    public String d(Widget w) { return w.getName(); }

    public void e(Widget w) { w.setCount(1); }

    public boolean f(Widget w) { return w.isReady(); }
}
