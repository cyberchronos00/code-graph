use lapin::{options::*, types::FieldTable, BasicProperties, Channel, ExchangeKind};
use rdkafka::consumer::{Consumer, StreamConsumer};

const LEDGER_EXCHANGE: &str = "ledger";

async fn post_entry(channel: &Channel, account: &str) -> lapin::Result<()> {
    channel
        .exchange_declare(LEDGER_EXCHANGE, ExchangeKind::Topic, ExchangeDeclareOptions::default(), FieldTable::default())
        .await?;
    channel
        .basic_publish(LEDGER_EXCHANGE, &format!("entry.{}", account), BasicPublishOptions::default(), b"x", BasicProperties::default())
        .await?;
    Ok(())
}

async fn bind_entries(channel: &Channel) -> lapin::Result<()> {
    let queue = channel
        .queue_declare("ledger.entries", QueueDeclareOptions::default(), FieldTable::default())
        .await?;
    channel
        .queue_bind(queue.name().as_str(), LEDGER_EXCHANGE, "entry.*", QueueBindOptions::default(), FieldTable::default())
        .await?;
    let _consumer = channel
        .basic_consume("ledger.entries", "ledger", BasicConsumeOptions::default(), FieldTable::default())
        .await?;
    Ok(())
}

fn consume_invoices(consumer: &StreamConsumer) {
    let topic = std::env::var("INVOICE_TOPIC").unwrap_or_else(|_| "billing.invoices".to_string());
    consumer.subscribe(&[topic.as_str()]).expect("subscribe");
}

fn main() {}
