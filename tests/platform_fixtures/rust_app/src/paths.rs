use std::path::PathBuf;

#[cfg(target_os = "windows")]
pub fn config_dir() -> PathBuf {
    PathBuf::from(std::env::var("APPDATA").unwrap_or_default())
}

#[cfg(unix)]
pub fn config_dir() -> PathBuf {
    xdg_home()
}

#[cfg(unix)]
fn xdg_home() -> PathBuf {
    PathBuf::from(std::env::var("XDG_CONFIG_HOME").unwrap_or_default())
}
