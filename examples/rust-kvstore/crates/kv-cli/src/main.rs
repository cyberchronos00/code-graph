//! `kv` command line tool.

use std::process::ExitCode;

use kv_core::{codec, open_default, Store, StoreError};

mod commands;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if std::env::var("KV_VERBOSE").is_ok() {
        eprintln!("kv {} (build {})", env!("CARGO_PKG_VERSION"), kv_core::BUILD_ID);
    }
    let mut store = open_default();
    match run(&args, store.as_mut()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e:?}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: &[String], store: &mut dyn Store) -> Result<(), StoreError> {
    let codec = codec::by_name(option_env!("KV_CODEC").unwrap_or("plain"));
    match args.first().map(String::as_str) {
        Some("get") => commands::get(store, &args[1]),
        Some("put") => commands::put(store, codec.as_ref(), &args[1], &args[2]),
        Some("rm") => store.delete(&args[1]).map(|_| ()),
        _ => {
            eprintln!("usage: kv get|put|rm KEY [VALUE]");
            Ok(())
        }
    }
}
