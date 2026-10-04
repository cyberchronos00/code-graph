use tokio::net::UdpSocket;

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let _c = peer::client::connect("127.0.0.1:6380").await?;
    let sock = UdpSocket::bind("0.0.0.0:0").await?;
    sock.send_to(b"hi", "127.0.0.1:5683").await?;
    Ok(())
}
