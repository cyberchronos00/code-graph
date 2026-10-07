import httpx
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/cover")
async def cover(url: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    return resp.json()


@app.get("/api/covers/{isbn}")
async def by_isbn(isbn: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://covers.bookstore.test/images/{isbn}.jpg")
    return resp.json()
