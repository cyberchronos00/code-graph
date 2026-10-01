//! kv-core: a tiny key-value store with pluggable backends and codecs.

pub mod codec;
pub mod store;
mod util;

pub use codec::{Codec, Plain};
pub use store::{MemoryStore, Store, StoreError};

/// Build id stamped by build.rs.
pub const BUILD_ID: &str = env!("KV_BUILD_ID");

/// Open the default store for this build: a file store when the `fs` feature is on.
pub fn open_default() -> Box<dyn Store> {
    #[cfg(feature = "fs")]
    {
        if let Some(dir) = store::file::data_dir() {
            return Box::new(store::file::FileStore::new(dir));
        }
    }
    Box::new(MemoryStore::default())
}

/// Copy every key from one store into another, encoding values with `codec`.
pub fn migrate<S: Store + ?Sized, D: Store + ?Sized>(src: &S, dst: &mut D, codec: &dyn Codec) -> Result<usize, StoreError> {
    let mut n = 0;
    for key in src.keys() {
        if let Some(v) = src.get(&key)? {
            dst.put(&key, &codec.encode(&v))?;
            n += 1;
        }
    }
    Ok(n)
}
