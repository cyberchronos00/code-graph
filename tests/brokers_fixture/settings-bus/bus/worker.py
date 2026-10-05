from aiokafka import AIOKafkaConsumer
import aio_pika

from .config import settings


async def run_events() -> None:
    consumer = AIOKafkaConsumer(bootstrap_servers="localhost:9092")
    consumer.subscribe([settings.events_topic])
    async for _msg in consumer:
        pass


async def run_jobs(channel) -> None:
    q = await channel.declare_queue(settings.jobs_queue)
    await q.consume(on_job)


async def on_job(msg: aio_pika.IncomingMessage) -> None:
    async with msg.process():
        pass
