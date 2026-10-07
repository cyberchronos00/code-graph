import socket
from urllib.parse import urlparse

import requests
from django.http import JsonResponse

from .feeds import pull_feed

ALLOWED_HOSTS = {"hooks.bookstore.test"}


def cover(request):
    target = request.GET.get("url")
    return JsonResponse(requests.get(target, timeout=5).json())


def by_isbn(request, isbn):
    resp = requests.get(f"https://covers.bookstore.test/images/{isbn}.jpg", timeout=5)
    return JsonResponse({"size": len(resp.content)})


def notify(request):
    callback = request.POST["callback"]
    parsed = urlparse(callback)
    if parsed.hostname not in ALLOWED_HOSTS:
        return JsonResponse({"error": "host not allowed"}, status=400)
    requests.post(callback, json={"ok": True}, timeout=5)
    return JsonResponse({"ok": True})


def import_feed(request):
    return JsonResponse(pull_feed(request.data.get("feed")))


def mirror(request):
    return JsonResponse({"address": socket.gethostbyname(request.GET["host"])})


def lookup(request):
    resp = requests.get("https://isbn.bookstore.test/lookup?code=" + request.GET.get("code", ""), timeout=5)
    return JsonResponse(resp.json())


def raw(request, link):
    return JsonResponse(requests.get(link, timeout=5).json())
