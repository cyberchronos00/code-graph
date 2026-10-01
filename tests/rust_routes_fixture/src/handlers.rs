pub async fn health() -> &'static str {
    "ok"
}

pub async fn list_items() -> String {
    load_items().join(",")
}

pub async fn create_item() -> &'static str {
    "created"
}

pub async fn update_item() -> &'static str {
    "updated"
}

fn load_items() -> Vec<String> {
    vec!["a".into(), "b".into()]
}
