fn main() {
    println!("{}", greet("world"));
}

pub fn greet(name: &str) -> String {
    format!("hello {name}")
}

#[cfg(test)]
mod tests {
    use super::greet;

    macro_rules! greets {
        ($name:ident, $who:expr, $want:expr) => {
            #[test]
            fn $name() {
                assert_eq!(greet($who), $want);
            }
        };
    }

    greets!(greets_world, "world", "hello world");
    greets!(greets_empty, "", "hello ");
}
