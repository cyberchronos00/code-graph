use axum::extract::ws::{WebSocket, WebSocketUpgrade};
use axum::response::Response;
use axum::routing::get;
use axum::Router;

pub fn app() -> Router {
    Router::new()
        .route("/ws", get(ws_handler))
        .route("/users/:id", get(user))
}

async fn ws_handler(ws: WebSocketUpgrade) -> Response {
    ws.on_upgrade(handle)
}

async fn handle(_socket: WebSocket) {}

async fn user() -> &'static str {
    "user"
}
