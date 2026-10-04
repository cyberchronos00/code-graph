import hashlib
import hmac
import json

import requests


def notify_shipped(url, secret, order):
    raw = json.dumps({"event": "order.shipped", "order": order})
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return requests.post(url, data=raw, headers={"X-Acme-Signature": sig})
