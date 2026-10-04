use serde_json::Value;
use socketioxide::extract::{Data, SocketRef};
use socketioxide::SocketIo;

mod http;

fn on_connect(socket: SocketRef) {
    socket.on("chat:send", on_send);
    socket.on("chat:history", |s: SocketRef, Data::<Value>(_d)| {
        s.emit("chat:history", &Vec::<String>::new()).ok();
    });
    socket.emit("room:joined", "lobby").ok();
}

fn on_send(s: SocketRef, Data(d): Data<Value>) {
    s.to("lobby").emit("chat:message", &d).ok();
}

fn on_admin(socket: SocketRef) {
    socket.emit("kick", "bye").ok();
}

#[tokio::main]
async fn main() {
    let (_layer, io) = SocketIo::new_layer();
    io.ns("/", on_connect);
    io.ns("/admin", on_admin);
}
