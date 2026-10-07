import requests


def cover_proxy_helper(request):
    target = request.GET.get("url")
    return requests.get(target, timeout=5)
