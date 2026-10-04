import hashlib
import hmac
import os

import stripe
from fastapi import FastAPI, Header, Request
from standardwebhooks import Webhook
from twilio.request_validator import RequestValidator

from .billing import activate, log, refund

app = FastAPI()


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    event = stripe.Webhook.construct_event(payload, stripe_signature, os.environ["STRIPE_WH"])
    if event["type"] == "checkout.session.completed":
        activate(event["data"]["object"])
    elif event["type"] == "charge.refunded":
        refund(event["data"]["object"])
    return {"ok": True}


@app.post("/webhooks/standard")
async def standard_webhook(request: Request):
    wh = Webhook(os.environ["WH_SECRET"])
    msg = wh.verify(await request.body(), dict(request.headers))
    if msg["type"] == "invoice.created":
        log("invoice")
    return {}


@app.post("/webhooks/github")
async def github_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    mac = "sha256=" + hmac.new(os.environ["GH_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, sig):
        return {"error": "bad signature"}
    kind = request.headers.get("X-GitHub-Event")
    if kind == "release":
        log("release")
    return {}


@app.post("/hooks/twilio")
async def twilio_sms(request: Request):
    form = await request.form()
    ok = RequestValidator(os.environ["TWILIO_TOKEN"]).validate(str(request.url), dict(form), request.headers.get("X-Twilio-Signature", ""))
    return {"ok": ok}


@app.post("/hooks/twilio-open")
async def twilio_open(request: Request):
    sig = request.headers.get("X-Twilio-Signature")
    return {"sig": sig}


@app.get("/health")
async def health():
    return {"ok": True}
