from decimal import Decimal


class Discount:
    def __init__(self, percent: int = 0):
        self.percent = percent

    def apply(self, amount: Decimal) -> Decimal:
        return amount * (100 - self.percent) / 100


def line_total(book, quantity: int, discount: Discount | None = None) -> float:
    amount = book.price * quantity
    if discount is not None:
        amount = discount.apply(amount)
    return float(amount)
