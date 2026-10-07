import os
import socket

OPEN_SOCK = "/run/bookshop/agent-open.sock"
PRIVATE_SOCK = "/run/bookshop/agent-private.sock"


def serve_open():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(OPEN_SOCK)
    os.chmod(OPEN_SOCK, 0o666)
    s.listen(1)
    conn, _ = s.accept()
    conn.sendall(conn.recv(64))


def serve_private():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(PRIVATE_SOCK)
    os.chmod(PRIVATE_SOCK, 0o600)
    s.listen(1)
    conn, _ = s.accept()
    conn.sendall(conn.recv(64))
