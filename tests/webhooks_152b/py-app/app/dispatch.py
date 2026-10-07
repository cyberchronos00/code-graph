import hashlib
import hmac
import json

import requests


def deliver(sub, payload):
    raw = json.dumps({"event": sub.event_type, "payload": payload})
    sig = hmac.new(sub.secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return requests.post(sub.url, data=raw, headers={"X-Bookstore-Signature": sig})
