from celery import shared_task

from .models import Book, Order


@shared_task
def send_order_confirmation(order_id):
    order = Order.objects.get(id=order_id)
    return order.customer_email


@shared_task
def recompute_rating(book_id):
    return Book.objects.filter(id=book_id).count()
