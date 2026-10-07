from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from .models import Order


def order_list(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    Order.objects.create(title=request.POST["title"])
    return JsonResponse({"ok": True})


def ledger(request):
    if not request.user.has_perm("bookshop.view_ledger"):
        return JsonResponse({"error": "forbidden"}, status=403)
    return JsonResponse({"rows": []})


def reports(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return JsonResponse({"rows": []})


def catalog(request):
    Order.objects.create(title=request.POST["title"])
    return JsonResponse({"ok": True})


def sitemap(request):
    return JsonResponse({"urls": []})


def lookup(request):
    owner = find_owner(request)
    Order.objects.create(title=owner)
    return JsonResponse({"ok": True})


def find_owner(request):
    return request.GET["owner"]
