use zbus::{interface, proxy};

pub struct Greeter {
    pub count: u64,
}

#[interface(name = "org.example.Greeter1")]
impl Greeter {
    async fn say_hello(&mut self, name: &str) -> String {
        self.count += 1;
        format!("Hello {}!", name)
    }

    #[zbus(name = "Reset")]
    fn reset_counter(&mut self) {
        self.count = 0;
    }
}

#[proxy(interface = "org.example.Greeter1", default_service = "org.example.Greeter", default_path = "/org/example/Greeter")]
trait GreeterClient {
    fn say_hello(&self, name: &str) -> zbus::Result<String>;
}

pub struct Settings {
    pub level: u32,
}

#[interface(name = "org.example.Settings1")]
impl Settings {
    /// The current level.
    #[zbus(property)]
    fn level(&self) -> u32 {
        fn clamp(v: u32) -> u32 {
            v.min(10)
        }
        clamp(self.level)
    }

    #[zbus(property)]
    fn set_level(&mut self, level: u32) {
        self.level = level;
    }

    fn r#match(&self, pattern: &str) -> bool {
        pattern.is_empty()
    }
}
