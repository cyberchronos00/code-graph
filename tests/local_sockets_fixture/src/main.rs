mod bus;
mod pipes;

use std::os::unix::fs::PermissionsExt;
use tokio::net::{UnixListener, UnixStream};

const CONTROL_SOCKET: &str = "/run/ipcd/control.sock";

pub async fn serve_control() -> std::io::Result<()> {
    let listener = UnixListener::bind(CONTROL_SOCKET)?;
    std::fs::set_permissions(CONTROL_SOCKET, std::fs::Permissions::from_mode(0o600))?;
    loop {
        let (_stream, _) = listener.accept().await?;
    }
}

pub async fn send_command() -> std::io::Result<()> {
    let _stream = UnixStream::connect(CONTROL_SOCKET).await?;
    Ok(())
}

fn main() {}

pub async fn grpc_client() -> Result<(), Box<dyn std::error::Error>> {
    let path = "unix:///run/ipcd/control.sock";
    let _channel = tonic::transport::Endpoint::try_from(path)?;
    let _web = tonic::transport::Endpoint::try_from("http://[::]:50051")?;
    Ok(())
}
