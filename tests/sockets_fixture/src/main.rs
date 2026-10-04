use peer::DEFAULT_PORT;
use tokio::net::{TcpListener, UdpSocket};

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let port = std::env::var("PEER_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(DEFAULT_PORT);
    let listener = TcpListener::bind(format!("0.0.0.0:{}", port)).await?;
    run(listener).await;
    let udp = UdpSocket::bind("127.0.0.1:5683").await?;
    drop(udp);
    Ok(())
}

async fn run(listener: TcpListener) {
    loop {
        let _ = listener.accept().await;
    }
}
