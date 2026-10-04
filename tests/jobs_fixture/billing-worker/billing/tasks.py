from celery import shared_task

from .app import app


@app.task(name="billing.charge", queue="billing")
def charge(order_id):
    return order_id


@app.task(name="billing.export.csv")
def export_csv(day):
    return day


@shared_task
def nightly_report():
    return 1


def close_day(day):
    export_csv.apply_async(args=[day])   # routed to `exports`: no worker of the Procfile consumes it
    nightly_report.delay()
    """
    charge.delay(1)   (docstring: not a dispatch)
    """
