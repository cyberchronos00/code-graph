use kv_core::{migrate, MemoryStore, Plain, Store};

#[test]
fn put_then_get() {
    let mut s = MemoryStore::default();
    s.put("a", b"1").unwrap();
    assert_eq!(s.get("a").unwrap(), Some(b"1".to_vec()));
}

#[test]
fn migrate_copies_keys() {
    let mut a = MemoryStore::default();
    a.put("k", b"v").unwrap();
    let mut b = MemoryStore::default();
    assert_eq!(migrate(&a, &mut b, &Plain).unwrap(), 1);
    assert!(b.contains("k"));
}
