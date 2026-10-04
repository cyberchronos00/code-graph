use std::process::Command;

pub struct Cli {
    pub cmd: Command,
}

impl Cli {
    pub fn arg(&mut self, a: &str) -> &mut Cli {
        self.cmd.arg(a);
        self
    }
}

pub fn setup(name: &str) -> Cli {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_mtest-fixture"));
    cmd.env("TEST_NAME", name);
    Cli { cmd }
}
