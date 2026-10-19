package booth;

public class FromJava {
    public String read(Cart c) { return c.getLabel(); }

    public void write(Cart c) { c.setItems(4); }
}
