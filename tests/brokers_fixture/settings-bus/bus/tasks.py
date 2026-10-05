"""AMQP publish via a helper whose queue parameter receives module constants at call sites (#137)."""
from aio_pika import Message

JOBS_A = "jobs.a"
JOBS_B = "jobs.b"


class TaskQueue:
    def __init__(self, channel):
        self._channel = channel

    async def publish_a(self, body: bytes) -> None:
        await self._publish(JOBS_A, body)

    async def publish_b(self, body: bytes) -> None:
        await self._publish(JOBS_B, body)

    async def _publish(self, queue: str, body: bytes) -> None:
        await self._channel.default_exchange.publish(Message(body), routing_key=queue)

    async def consume_a(self, handler) -> None:
        q = await self._channel.declare_queue(JOBS_A)
        await q.consume(handler)
