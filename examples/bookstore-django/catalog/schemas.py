from typing import List, Optional

from ninja import ModelSchema, Schema

from .models import Book


class AuthorOut(Schema):
    id: int
    name: str


class BookOut(ModelSchema):
    author: AuthorOut

    class Meta:
        model = Book
        fields = ["id", "title", "price", "format", "subtitle"]


class BookIn(Schema):
    title: str
    author_id: int
    price: float
    format: str = "paperback"
    subtitle: Optional[str] = None


class OrderLineIn(Schema):
    book_id: int
    quantity: int


class OrderIn(Schema):
    customer_email: str
    lines: List[OrderLineIn]
    gift: bool = False
    note: str = ""


class OrderOut(Schema):
    id: int
    status: str
    total: float
    created_at: str
