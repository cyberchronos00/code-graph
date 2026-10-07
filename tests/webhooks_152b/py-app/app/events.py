from enum import Enum


class WebhookEvent(str, Enum):
    ORDER_PAID = "order.paid"
    BOOK_RESTOCKED = "book.restocked"
