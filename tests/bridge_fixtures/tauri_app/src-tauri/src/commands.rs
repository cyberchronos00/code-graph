use tauri::command;

static mut COUNT: u32 = 0;

#[command]
pub async fn increment() -> u32 {
    bump()
}

fn bump() -> u32 {
    unsafe { COUNT += 1; COUNT }
}

// not registered in generate_handler!
#[tauri::command]
pub fn secret() -> String {
    String::from("s")
}
