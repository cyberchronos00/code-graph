use tokio::net::windows::named_pipe::{ClientOptions, ServerOptions};

const PIPE_NAME: &str = r"\\.\pipe\ipcd-events";

pub async fn pipe_server() -> std::io::Result<()> {
    let _server = ServerOptions::new().first_pipe_instance(true).create(PIPE_NAME)?;
    Ok(())
}

pub async fn pipe_client() -> std::io::Result<()> {
    let _client = ClientOptions::new().open(PIPE_NAME)?;
    Ok(())
}
