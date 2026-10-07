import hashlib
import hmac
import os

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Order
from .notify import send_receipt


@login_required
def create_order(request):
    Order.objects.create(title=request.POST["title"])
    send_receipt()
    return JsonResponse({"ok": True})


@csrf_exempt
def purge_orders(request):
    Order.objects.all().delete()
    return JsonResponse({"purged": True})


def health(request):
    return JsonResponse({"ok": True})


@csrf_exempt
def github_open(request):
    kind = request.headers.get("X-GitHub-Event")
    return JsonResponse({"seen": kind})


@csrf_exempt
def github_signed(request):
    mac = "sha256=" + hmac.new(os.environ["GH_SECRET"].encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, request.headers.get("X-Hub-Signature-256", "")):
        return JsonResponse({"error": "bad signature"}, status=401)
    return JsonResponse({"ok": True})
