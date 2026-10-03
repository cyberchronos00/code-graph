import socketio

sio = socketio.AsyncClient()


async def publish_order(order_id: int, total: int):
    await sio.emit("order:created", {"id": order_id, "total": total}, namespace="/orders")


async def publish_audit(order_id: int):
    await sio.emit("audit:order", {"id": order_id})      # handled by an audit service outside these repos
