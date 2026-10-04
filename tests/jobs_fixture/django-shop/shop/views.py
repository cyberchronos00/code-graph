from django.http import JsonResponse

from proj.celery import app

from .tasks import send_invoice


def place_order(request):
    send_invoice.delay(1)
    app.send_task("billing.charge", args=[1])
    return JsonResponse({"ok": True})
