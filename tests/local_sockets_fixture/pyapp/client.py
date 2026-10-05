import asyncio
import socket

from .config import AGENT_SOCKET


async def ask_agent():
    reader, writer = await asyncio.open_unix_connection(AGENT_SOCKET)
    writer.write(b"hi")


def ask_status():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect("/tmp/status.sock")
    return s.recv(10)


def send_event(kind):
    with open("/tmp/events.fifo", "w") as fifo:
        fifo.write(kind + "\n")


def read_log():
    with open("/tmp/events.log", "w") as log:     # not a FIFO: no edge
        log.write("x")


def grpc_agent():
    import grpc
    return grpc.insecure_channel("unix:///run/worker/agent.sock")
