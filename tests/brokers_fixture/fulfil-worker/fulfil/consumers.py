import pika
import redis
from confluent_kafka import Consumer

from fulfil.settings import AUDIT_TOPIC, ORDERS_TOPIC


def run_kafka():
    consumer = Consumer({"bootstrap.servers": "kafka:9092", "group.id": "fulfilment"})
    consumer.subscribe([ORDERS_TOPIC, AUDIT_TOPIC])
    while True:
        msg = consumer.poll(1.0)
        if msg is not None:
            handle_order(msg.value())


def handle_order(value):
    return value


def on_region_order(ch, method, properties, body):
    return body


def on_invoice(ch, method, properties, body):
    return body


def run_rabbit():
    conn = pika.BlockingConnection(pika.ConnectionParameters("rabbit"))
    channel = conn.channel()
    channel.exchange_declare(exchange="shop.events", exchange_type="topic")
    result = channel.queue_declare(queue="", exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(exchange="shop.events", queue=queue_name, routing_key="order.eu.*")
    channel.basic_consume(queue=queue_name, on_message_callback=on_region_order, auto_ack=True)
    channel.queue_declare(queue="invoices", durable=True)
    channel.basic_consume(queue="invoices", on_message_callback=on_invoice)
    channel.start_consuming()


def invalidate(message):
    return message["data"]


def run_redis():
    r = redis.Redis(host="redis")
    p = r.pubsub()
    p.subscribe(**{"cache:invalidate": invalidate})
    p.psubscribe("metrics:*")
    r.publish("fulfil:started", "1")
