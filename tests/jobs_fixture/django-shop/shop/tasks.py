from celery import shared_task


@shared_task
def send_invoice(order_id):
    return order_id
