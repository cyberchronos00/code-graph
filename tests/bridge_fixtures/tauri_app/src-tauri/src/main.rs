mod commands;
mod menu;

use commands::{increment, secret};

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}

fn main() {
    tauri::Builder::default()
        .plugin(menu::init())
        .invoke_handler(tauri::generate_handler![greet, commands::increment])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
