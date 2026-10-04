use crate::util::Cli;

clitest!(prints_hello, |mut cmd: Cli| {
    cmd.arg("--verbose");
});

clitest!(no_args, |_cmd: Cli| {});
