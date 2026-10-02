mod paths;
#[cfg(windows)]
mod win;

use std::path::PathBuf;

fn main() {
    let dir = paths::config_dir();
    println!("{}", dir.display());
    notify("ready");
    #[cfg(windows)]
    win::set_console_title("demo");
    if cfg!(target_os = "macos") {
        dock_badge();
    } else {
        tray_hint();
    }
    open_logs(&dir);
}

fn notify(msg: &str) {
    println!("{msg}");
}

#[cfg(target_os = "macos")]
fn dock_badge() {}

fn tray_hint() {}

// declared only for linux: a caller on other targets has no implementation
#[cfg(target_os = "linux")]
fn open_logs(dir: &PathBuf) {
    let _ = dir;
}
