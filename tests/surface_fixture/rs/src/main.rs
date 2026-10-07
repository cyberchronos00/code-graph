use std::io::Write;
use std::net::TcpListener;

fn serve_feed() -> std::io::Result<()> {
    let listener = TcpListener::bind("0.0.0.0:7400")?;
    for stream in listener.incoming() {
        handle_feed(stream?);
    }
    Ok(())
}

fn serve_admin() -> std::io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:7401")?;
    for stream in listener.incoming() {
        stream?.write_all(b"ok")?;
    }
    Ok(())
}

fn handle_feed(mut stream: std::net::TcpStream) {
    let _ = stream.write_all(b"feed");
}

fn main() {
    let _ = serve_feed();
    let _ = serve_admin();
}
