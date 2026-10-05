import asyncio
import os
import socket
import socketserver

from .config import AGENT_SOCKET


class StatusHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"ok")


def serve_status():
    server = socketserver.UnixStreamServer("/tmp/status.sock", StatusHandler)
    server.serve_forever()


async def on_agent(reader, writer):
    writer.write(await reader.read(100))


async def serve_agent():
    server = await asyncio.start_unix_server(on_agent, path=AGENT_SOCKET)
    os.chmod(AGENT_SOCKET, 0o660)
    await server.serve_forever()


def raw_server():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind("/tmp/raw.sock")
    s.listen(1)


def make_fifo():
    os.mkfifo("/tmp/events.fifo")
