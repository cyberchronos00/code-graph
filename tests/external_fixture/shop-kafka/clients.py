import os

import aio_pika
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from confluent_kafka import Producer
from kafka import KafkaProducer

from settings import settings


async def open_kafka():
    return AIOKafkaProducer(bootstrap_servers=settings.kafka_url)


async def open_consumer():
    return AIOKafkaConsumer("orders", bootstrap_servers=["redpanda:9092"])


def open_pykafka():
    return KafkaProducer(bootstrap_servers=os.environ["KAFKA_URL"])


def open_confluent():
    return Producer({"bootstrap.servers": "broker.internal.example:9092"})


async def open_amqp():
    return await aio_pika.connect_robust(settings.amqp_url)
