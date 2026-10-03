use tauri::{
    command,
    plugin::{Builder, TauriPlugin},
    Runtime,
};

#[command]
pub fn popup() {}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("app-menu")
        .invoke_handler(tauri::generate_handler![
            #![plugin(app_menu)]
            popup
        ])
        .build()
}
