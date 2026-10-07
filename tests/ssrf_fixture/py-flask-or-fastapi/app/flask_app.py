import socket
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, jsonify, request

from .helpers import pull_feed

app = Flask(__name__)
ALLOWED_HOSTS = {"hooks.bookstore.test"}


@app.get("/cover")
def cover():
    target = request.args.get("url")
    return jsonify(requests.get(target, timeout=5).json())


@app.get("/covers/<isbn>")
def by_isbn(isbn):
    return jsonify(requests.get("https://covers.bookstore.test/images/%s.jpg" % isbn, timeout=5).json())


@app.post("/notify")
def notify():
    data = request.get_json()
    callback = data["callback"]
    if urlparse(callback).hostname not in ALLOWED_HOSTS:
        return jsonify({"error": "host not allowed"}), 400
    requests.post(callback, json={"ok": True}, timeout=5)
    return jsonify({"ok": True})


@app.post("/import")
def import_feed():
    return jsonify(pull_feed(request.json["feed"]))


@app.get("/mirror")
def mirror():
    host = request.args["host"]
    return jsonify({"address": socket.gethostbyname(host)})


@app.get("/lookup")
def lookup():
    code = request.args.get("code")
    return jsonify(requests.get(f"https://isbn.bookstore.test/lookup?code={code}", timeout=5).json())


@app.post("/join")
def join():
    ref = request.form["ref"]
    return jsonify(requests.get(urljoin("https://catalog.bookstore.test/", ref), timeout=5).json())
