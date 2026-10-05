import os

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]


def charge(amount: int):
    return stripe.Charge.create(amount=amount, currency="usd", source="tok_fixture")
