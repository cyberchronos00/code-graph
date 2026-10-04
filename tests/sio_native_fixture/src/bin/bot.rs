use rust_socketio::{ClientBuilder, Payload, RawClient};
use serde_json::json;

fn on_message(payload: Payload, _c: RawClient) {
    println!("{:?}", payload);
}

fn main() {
    let client = ClientBuilder::new("http://localhost:3000")
        .on("chat:message", on_message)
        .connect()
        .expect("connection failed");
    client.emit("chat:send", json!({"text": "hi"})).expect("emit");
}
