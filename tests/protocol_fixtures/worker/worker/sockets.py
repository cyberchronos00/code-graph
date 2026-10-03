import socketio

from worker.models import Order, Shipment

sio = socketio.AsyncServer(async_mode="asgi")


@sio.on("connect", namespace="/orders")
async def connect(sid, environ, auth):
    if not auth or auth.get("token") != "secret-token":
        raise ConnectionRefusedError("unauthorized")


@sio.on("order:created", namespace="/orders")
async def on_order_created(sid, data):
    Order.objects.create(total=data["total"], status="new")


@sio.on("order:shipped", namespace="/orders")
async def on_order_shipped(sid, data):
    Shipment.objects.create(order_id=data["id"])


@sio.event
async def ping(sid, data):            # default namespace: no connect handler, nothing sends it
    return "pong"
