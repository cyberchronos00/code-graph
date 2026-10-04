pub const DEFAULT_PORT: u16 = 6380;

pub mod client {
    use tokio::net::TcpStream;

    pub async fn connect(addr: &str) -> std::io::Result<TcpStream> {
        TcpStream::connect(addr).await
    }
}
