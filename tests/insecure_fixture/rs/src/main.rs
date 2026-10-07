use reqwest::Client;
use tonic::transport::Channel;

fn lenient_client() -> Client {
    Client::builder().danger_accept_invalid_certs(true).build().unwrap()
}

fn lenient_hosts() -> Client {
    Client::builder().danger_accept_invalid_hostnames(true).build().unwrap()
}

fn strict_client() -> Client {
    Client::builder().danger_accept_invalid_certs(false).build().unwrap()
}

fn inventory_channel() -> Channel {
    Channel::from_static("http://inventory.bookstore.example:50051").connect_lazy()
}

fn tls_inventory_channel() -> Channel {
    Channel::from_static("https://inventory.bookstore.example:443").connect_lazy()
}

fn local_inventory_channel() -> Channel {
    Channel::from_static("http://127.0.0.1:50051").connect_lazy()
}

fn main() {
    let _ = (lenient_client(), lenient_hosts(), strict_client());
    let _ = (inventory_channel(), tls_inventory_channel(), local_inventory_channel());
}
