import requests
from flask import request


def cover_proxy_helper():
    target = request.args.get("url")
    return requests.get(target, timeout=5)
