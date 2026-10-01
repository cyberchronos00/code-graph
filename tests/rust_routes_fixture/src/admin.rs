#![cfg(feature = "admin")]
use actix_web::{get, HttpResponse};
use axum::{routing::get as aget, Router};

pub fn router() -> Router {
    Router::new().route("/stats", aget(stats))
}

async fn stats() -> &'static str {
    "stats"
}

#[get("/legacy/ping")]
async fn legacy_ping() -> HttpResponse {
    HttpResponse::Ok().finish()
}
