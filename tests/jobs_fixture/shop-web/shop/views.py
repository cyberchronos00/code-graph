from celery import Celery
from flask import Flask, request
from redis import Redis
from rq import Queue

from .settings import BILLING_QUEUE

app = Flask(__name__)
celery_app = Celery("shop", broker="redis://localhost:6379/0")
emails = Queue("emails", connection=Redis())


@app.post("/orders")
def create_order():
    order_id = request.json["id"]
    celery_app.send_task("billing.charge", args=[order_id], queue=BILLING_QUEUE)
    emails.enqueue("mailer.jobs.send_receipt", order_id)
    return {"ok": True}


@app.post("/refunds")
def refund():
    # nothing in the worker repo registers billing.refund: no_receiver after cg link
    celery_app.send_task("billing.refund", args=[request.json["id"]])
    return {"ok": True}


@app.post("/signup")
def signup():
    emails.enqueue(f"mailer.jobs.send_{request.json['kind']}", request.json["user"])
    return {"ok": True}
