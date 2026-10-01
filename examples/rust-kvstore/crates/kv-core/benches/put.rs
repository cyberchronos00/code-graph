use std::time::Instant;

use kv_core::{MemoryStore, Store};

fn main() {
    let mut s = MemoryStore::default();
    let t = Instant::now();
    for i in 0..10_000 {
        s.put(&format!("k{i}"), b"value").unwrap();
    }
    println!("10k puts in {:?}", t.elapsed());
}
