from fastapi import FastAPI

from app.events import publish_audit, publish_order, sio

app = FastAPI()


@app.post("/orders")
async def create_order(payload: dict):
    await publish_order(payload["id"], payload["total"])
    await publish_audit(payload["id"])
    return {"ok": True}


@app.post("/orders/{order_id}/status")
async def set_status(order_id: int, status: str):
    await sio.emit(f"order:{status}", {"id": order_id}, namespace="/orders")     # template: matches several receivers
    return {"ok": True}


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int):
    await sio.emit("order:cancelled", {"id": order_id}, namespace="/orders")   # nothing receives it
    return {"ok": True}
