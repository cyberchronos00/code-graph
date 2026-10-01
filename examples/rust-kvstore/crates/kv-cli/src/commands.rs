use kv_core::{Codec, Store, StoreError};

pub fn get(store: &dyn Store, key: &str) -> Result<(), StoreError> {
    match store.get(key)? {
        Some(v) => println!("{}", String::from_utf8_lossy(&v)),
        None => println!("(missing)"),
    }
    Ok(())
}

pub fn put(store: &mut dyn Store, codec: &dyn Codec, key: &str, value: &str) -> Result<(), StoreError> {
    store.put(key, &codec.encode(value.as_bytes()))
}
