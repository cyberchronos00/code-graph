import requests


def pull_feed(feed_url):
    return requests.get(feed_url, timeout=5).json()
