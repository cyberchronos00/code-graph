import grpc
import httpx
import paramiko
import requests
import ssl


def sync_catalog():
    return requests.get("https://catalog.bookstore.example/books", verify=False)


def sync_catalog_checked():
    return requests.get("https://catalog.bookstore.example/books", verify=True)


def fetch_prices():
    with httpx.Client(verify=False) as client:
        return client.get("https://prices.bookstore.example/today")


def legacy_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def strict_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    return ctx


def unverified_helper():
    return ssl._create_unverified_context()


def push_stock(host):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username="stock-sync")
    return client


def push_stock_pinned(host):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(host, username="stock-sync")
    return client


def inventory_channel():
    return grpc.insecure_channel("inventory.bookstore.example:50051")


def inventory_channel_tls(creds):
    return grpc.secure_channel("inventory.bookstore.example:443", creds)


def local_channel():
    return grpc.insecure_channel("localhost:50051")


def socket_channel():
    return grpc.insecure_channel("unix:/run/bookshop/inventory.sock")


def serve(server):
    server.add_insecure_port("[::]:50052")
    server.add_insecure_port("unix:/run/bookshop/serve.sock")
