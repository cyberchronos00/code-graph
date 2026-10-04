#[macro_export]
macro_rules! clitest {
    ($name:ident, $fun:expr) => {
        #[test]
        fn $name() {
            let cmd = crate::util::setup(stringify!($name));
            $fun(cmd);
        }
    };
}
