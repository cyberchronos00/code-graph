// Build script: stamps a build id into the crate (read back with env!).
fn main() {
    let id = std::env::var("KV_BUILD_ID").unwrap_or_else(|_| "dev".to_string());
    println!("cargo:rustc-env=KV_BUILD_ID={id}");
    println!("cargo:rerun-if-env-changed=KV_BUILD_ID");
}
