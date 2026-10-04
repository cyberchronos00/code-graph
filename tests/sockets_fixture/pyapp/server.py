"""A TCP job server (socketserver) and a UDP metrics listener that joins a multicast group."""
import socket
import socketserver
import struct

from .config import JOBS_PORT, METRICS_PORT

GROUP = "239.1.2.3"


class JobHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(1024))


def serve_jobs():
    with socketserver.TCPServer(("0.0.0.0", JOBS_PORT), JobHandler) as srv:
        srv.serve_forever()


def listen_metrics():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", METRICS_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    while True:
        handle_metric(sock.recv(512))


def handle_metric(data):
    return data


def admin_console(port=9900):
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
