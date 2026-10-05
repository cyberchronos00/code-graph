from aiokafka import AIOKafkaProducer
from aio_pika import Message

from .config import settings


async def publish_event(producer: AIOKafkaProducer, body: bytes) -> None:
    await producer.send_and_wait(settings.events_topic, body)


async def publish_job(channel, body: bytes) -> None:
    await channel.default_exchange.publish(Message(body), routing_key=settings.jobs_queue)


class EventBus:
    def __init__(self, settings_obj=settings):
        self.settings = settings_obj

    async def publish(self, producer: AIOKafkaProducer, body: bytes) -> None:
        await producer.send_and_wait(self.settings.events_topic, body)

async def publish_audit(producer: AIOKafkaProducer, body: bytes) -> None:
    await producer.send_and_wait(settings.audit_topic, body)

