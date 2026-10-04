import dramatiq
from rq import Queue


@dramatiq.actor(queue_name="digest", max_retries=3)
def send_digest(user_id):
    return user_id


@dramatiq.actor(actor_name="mailer.bounce", queue_name="bounces")
def handle_bounce(address):
    return address


def daily():
    send_digest.send(1)
    handle_bounce.send_with_options(args=("a@b.c",), delay=10)
