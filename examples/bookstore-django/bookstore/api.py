from ninja import NinjaAPI
from ninja.security import HttpBearer

from catalog.api import books_router, orders_router


class TokenAuth(HttpBearer):
    def authenticate(self, request, token):
        return token if token == "secret" else None


api = NinjaAPI(title="Bookstore API", version="1.0")
api.add_router("/books/", books_router)
api.add_router("/orders/", orders_router, auth=TokenAuth())
