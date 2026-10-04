// jsonrpsee: a proc-macro trait, its server impl and a registered closure.
use jsonrpsee::core::RpcResult;
use jsonrpsee::proc_macros::rpc;

#[rpc(server, client, namespace = "state")]
pub trait Rpc {
    #[method(name = "getKeys")]
    async fn storage_keys(&self, prefix: String) -> RpcResult<Vec<String>>;
}

pub struct RpcServerImpl;

#[async_trait::async_trait]
impl RpcServer for RpcServerImpl {
    async fn storage_keys(&self, prefix: String) -> RpcResult<Vec<String>> {
        Ok(vec![prefix])
    }
}

pub fn build_module(module: &mut jsonrpsee::RpcModule<()>) {
    module.register_method("say_hello", |_, _, _| "lo").unwrap();
}
