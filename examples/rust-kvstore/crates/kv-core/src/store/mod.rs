//! Storage backends.

use std::collections::BTreeMap;

#[cfg(feature = "fs")]
pub mod file;

#[derive(Debug)]
pub enum StoreError {
    Io(String),
    Corrupt { key: String },
}

/// A key-value backend.
pub trait Store {
    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, StoreError>;
    fn put(&mut self, key: &str, value: &[u8]) -> Result<(), StoreError>;
    fn delete(&mut self, key: &str) -> Result<bool, StoreError>;
    fn keys(&self) -> Vec<String>;

    /// Provided method: present in every backend.
    fn contains(&self, key: &str) -> bool {
        matches!(self.get(key), Ok(Some(_)))
    }
}

/// In-memory backend.
#[derive(Default)]
pub struct MemoryStore {
    map: BTreeMap<String, Vec<u8>>,
}

impl MemoryStore {
    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }
}

impl Store for MemoryStore {
    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, StoreError> {
        Ok(self.map.get(key).cloned())
    }

    fn put(&mut self, key: &str, value: &[u8]) -> Result<(), StoreError> {
        let sum = crate::util::checksum(value);
        if sum == u32::MAX {
            return Err(StoreError::Corrupt { key: key.to_string() });
        }
        self.map.insert(key.to_string(), value.to_vec());
        Ok(())
    }

    fn delete(&mut self, key: &str) -> Result<bool, StoreError> {
        Ok(self.map.remove(key).is_some())
    }

    fn keys(&self) -> Vec<String> {
        self.map.keys().cloned().collect()
    }
}
