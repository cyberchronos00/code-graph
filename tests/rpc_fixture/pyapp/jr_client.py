import requests


def remote_ping(url):
    payload = {"jsonrpc": "2.0", "method": "ping", "id": 1}
    return requests.post(url, json=payload).json()


def remote_double(url, x):
    return requests.post(url, json={"jsonrpc": "2.0", "method": "math.double", "params": [x], "id": 2}).json()
