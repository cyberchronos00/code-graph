import httpx


def pull_feed(feed_url):
    return httpx.get(feed_url, timeout=5).json()
