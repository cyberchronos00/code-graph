//! File-per-key backend (feature `fs`).

use std::fs;
use std::path::PathBuf;

use super::{Store, StoreError};

/// Data directory from `KV_DATA_DIR`, if set.
pub fn data_dir() -> Option<PathBuf> {
    std::env::var("KV_DATA_DIR").ok().map(PathBuf::from)
}

pub struct FileStore {
    root: PathBuf,
}

impl FileStore {
    pub fn new(root: PathBuf) -> Self {
        FileStore { root }
    }

    fn path_for(&self, key: &str) -> PathBuf {
        self.root.join(crate::util::sanitize(key))
    }
}

impl Store for FileStore {
    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, StoreError> {
        match fs::read(self.path_for(key)) {
            Ok(v) => Ok(Some(v)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(StoreError::Io(e.to_string())),
        }
    }

    fn put(&mut self, key: &str, value: &[u8]) -> Result<(), StoreError> {
        fs::write(self.path_for(key), value).map_err(|e| StoreError::Io(e.to_string()))
    }

    fn delete(&mut self, key: &str) -> Result<bool, StoreError> {
        Ok(fs::remove_file(self.path_for(key)).is_ok())
    }

    fn keys(&self) -> Vec<String> {
        fs::read_dir(&self.root)
            .map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
            .unwrap_or_default()
    }
}
