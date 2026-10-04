import asyncio
import os
import socket

from .config import JOBS_PORT


class MetricsClient:
    def __init__(self, host="localhost", port=8125):
        self.addr = (host, port)

    def send(self, line):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(line.encode(), self.addr)


def submit_job(payload):
    with socket.create_connection(("jobs.internal", JOBS_PORT)) as s:
        s.sendall(payload)


async def ask_cache():
    reader, writer = await asyncio.open_connection("cache", os.environ.get("CACHE_PORT"))
    writer.close()


def ephemeral():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
