from typing import List

from django.shortcuts import get_object_or_404
from ninja import Router

from .models import Book, Order, OrderLine
from .pricing import Discount, line_total
from .schemas import BookIn, BookOut, OrderIn, OrderOut
from .tasks import send_order_confirmation

books_router = Router(tags=["books"])
orders_router = Router(tags=["orders"])


@books_router.get("/", response=List[BookOut])
def list_books(request, q: str = ""):
    return Book.objects.filter(title__icontains=q).select_related("author")


@books_router.get("/{book_id}/", response=BookOut)
def get_book(request, book_id: int):
    return get_object_or_404(Book, id=book_id)


@books_router.post("/", response={201: BookOut})
def create_book(request, payload: BookIn):
    book = Book.objects.create(**payload.dict())
    return 201, book


@books_router.get("/{book_id}/availability/")
def availability(request, book_id: int):
    book = get_object_or_404(Book, id=book_id)
    return {"book_id": book.id, "in_stock": book.in_stock, "restock_date": None}


@orders_router.post("/", response=OrderOut)
def place_order(request, payload: OrderIn):
    order = Order.objects.create(customer_email=payload.customer_email)
    total = 0
    for line in payload.lines:
        book = get_object_or_404(Book, id=line.book_id)
        OrderLine.objects.create(order=order, book=book, quantity=line.quantity)
        total += line_total(book, line.quantity, Discount(10 if payload.gift else 0))
    send_order_confirmation.delay(order.id)
    return {"id": order.id, "status": order.status, "total": total, "created_at": order.created_at.isoformat()}


@orders_router.get("/{order_id}/", response=OrderOut)
def get_order(request, order_id: int):
    order = get_object_or_404(Order, id=order_id)
    return {"id": order.id, "status": order.status, "total": 0.0, "created_at": order.created_at.isoformat()}
