//! C ABI for kv-core.

use std::ffi::{c_char, c_int, CStr};

use kv_core::{MemoryStore, Store};

extern "C" {
    fn strlen(s: *const c_char) -> usize;
}

/// Opaque handle handed to C callers.
pub struct KvHandle {
    inner: MemoryStore,
}

#[no_mangle]
pub extern "C" fn kv_open() -> *mut KvHandle {
    Box::into_raw(Box::new(KvHandle { inner: MemoryStore::default() }))
}

/// # Safety
/// `h` must come from `kv_open`; `key` and `value` must be NUL-terminated.
#[no_mangle]
pub unsafe extern "C" fn kv_put(h: *mut KvHandle, key: *const c_char, value: *const c_char) -> c_int {
    let h = &mut *h;
    let k = CStr::from_ptr(key).to_string_lossy();
    let len = strlen(value);
    let v = std::slice::from_raw_parts(value as *const u8, len);
    match h.inner.put(&k, v) {
        Ok(()) => 0,
        Err(_) => -1,
    }
}

/// # Safety
/// `h` must come from `kv_open` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn kv_close(h: *mut KvHandle) {
    if !h.is_null() {
        drop(Box::from_raw(h));
    }
}
