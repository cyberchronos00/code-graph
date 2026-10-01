//! Value codecs. Called through `&dyn Codec` (dynamic dispatch) and `T: Codec` (static dispatch).

pub trait Codec {
    fn encode(&self, raw: &[u8]) -> Vec<u8>;
    fn decode(&self, data: &[u8]) -> Vec<u8>;
}

/// Identity codec.
pub struct Plain;

impl Codec for Plain {
    fn encode(&self, raw: &[u8]) -> Vec<u8> {
        raw.to_vec()
    }
    fn decode(&self, data: &[u8]) -> Vec<u8> {
        data.to_vec()
    }
}

/// Run-length codec (feature `compression`).
#[cfg(feature = "compression")]
pub struct Rle;

#[cfg(feature = "compression")]
impl Codec for Rle {
    fn encode(&self, raw: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let b = raw[i];
            let mut n = 1u8;
            while i + (n as usize) < raw.len() && raw[i + n as usize] == b && n < u8::MAX {
                n += 1;
            }
            out.push(n);
            out.push(b);
            i += n as usize;
        }
        out
    }
    fn decode(&self, data: &[u8]) -> Vec<u8> {
        data.chunks(2).flat_map(|c| std::iter::repeat(c[1]).take(c[0] as usize)).collect()
    }
}

/// Static dispatch: the concrete codec is known at each call site.
pub fn roundtrip<C: Codec>(codec: &C, raw: &[u8]) -> Vec<u8> {
    codec.decode(&codec.encode(raw))
}

/// Pick a codec by name.
pub fn by_name(name: &str) -> Box<dyn Codec> {
    match name {
        #[cfg(feature = "compression")]
        "rle" => Box::new(Rle),
        _ => Box::new(Plain),
    }
}
