from channels.generic.websocket import AsyncJsonWebsocketConsumer


class StockConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("stock", self.channel_name)
        await self.accept()

    async def stock_update(self, event):
        await self.send_json({"book_id": event["book_id"], "in_stock": event["in_stock"]})
