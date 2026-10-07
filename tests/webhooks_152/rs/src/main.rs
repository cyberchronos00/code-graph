use axum::routing::post;
use axum::Router;

fn deploy() {}
fn issues() {}

async fn github_hook() {
    let event_name = headers_get("X-GitHub-Event");
    match event_name {
        Some("push") => deploy(),
        Some("issues") | Some("pull_request") => issues(),
        _ => {}
    }
    let mode = "sync";
    match mode {
        "sync" => deploy(),
        "async" => issues(),
        _ => {}
    }
}

async fn stripe_hook() {
    let _sig = headers_get("Stripe-Signature");
    match event["type"].as_str() {
        Some("checkout.session.completed") => deploy(),
        Some("invoice.paid") => issues(),
        _ => {}
    }
}

fn headers_get(name: &str) -> Option<&str> {
    let _ = name;
    None
}

fn router() -> Router {
    Router::new()
        .route("/webhooks/github", post(github_hook))
        .route("/webhooks/stripe", post(stripe_hook))
}
