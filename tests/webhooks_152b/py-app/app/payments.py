import requests
from django.conf import settings
from urllib.parse import urljoin


def create_payment(store, order):
    url = settings.BASE_URL + "/api/webhooks/payments/" + store.slug
    return requests.post("https://pay.provider.test/v1/payments", json={"amount": order.total, "callback_url": url})


def create_partner_payment(order):
    return requests.post("https://pay.provider.test/v1/partner",
                         json={"callback_url": urljoin(settings.PARTNER_URL, "/api/webhooks/payments/x")})


def register_sdk(client):
    client.webhooks.create(url=f"{settings.BASE_URL}/api/webhooks/payments/main")
