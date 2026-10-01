use axum::{routing::{get, post}, Router};

mod admin;
mod handlers;

const PORT_VAR: &str = "APP_PORT";

#[tokio::main]
async fn main() {
    let port = std::env::var(PORT_VAR).unwrap_or_else(|_| "3000".into());
    let app = Router::new()
        .route("/health", get(handlers::health))
        .route("/items", get(handlers::list_items).post(handlers::create_item))
        .route("/items/:id", post(handlers::update_item));
    #[cfg(feature = "admin")]
    let app = app.nest("/admin", admin::router());
    serve(app, &port).await;
}

async fn serve(_app: Router, _port: &str) {}
