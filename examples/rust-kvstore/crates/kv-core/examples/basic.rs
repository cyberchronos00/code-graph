use kv_core::{open_default, Store};

fn main() {
    let mut store = open_default();
    store.put("hello", b"world").unwrap();
    println!("{:?}", store.get("hello").unwrap());
}
