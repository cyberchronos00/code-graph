import asyncio

import websockets
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI()


async def gen():
    yield "data: 1\n\n"


async def handler(websocket):
    async for message in websocket:
        await websocket.send(message)


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()


@app.get("/stream")
async def stream():
    return EventSourceResponse(gen())


@app.get("/raw")
async def raw():
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/csv")
async def csv():
    return StreamingResponse(gen(), media_type="text/csv")


@app.websocket("/ws")
async def ws(websocket):
    await websocket.accept()
