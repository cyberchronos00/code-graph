//! Internal helpers.

/// Additive checksum over the bytes (reads through a raw pointer).
pub(crate) fn checksum(data: &[u8]) -> u32 {
    let mut sum: u32 = 0;
    let p = data.as_ptr();
    for i in 0..data.len() {
        // SAFETY: i < data.len()
        let b = unsafe { *p.add(i) };
        sum = sum.wrapping_add(b as u32);
    }
    sum
}

/// Make a key safe to use as a file name.
pub(crate) fn sanitize(key: &str) -> String {
    key.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '_' }).collect()
}

/// Never called: kept to show dead-code reporting.
#[allow(dead_code)]
pub(crate) fn legacy_hash(data: &[u8]) -> u64 {
    data.iter().fold(0u64, |h, b| h.wrapping_mul(31).wrapping_add(*b as u64))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_adds_bytes() {
        assert_eq!(checksum(&[1, 2, 3]), 6);
    }
}
