from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, viewsets
from rest_framework.views import APIView

from .models import Coupon


@login_required
def redeem(request, code):
    Coupon.objects.filter(code=code).update(used=True)
    return JsonResponse({"ok": True})


@csrf_exempt
def reset(request):
    Coupon.objects.all().update(used=False)
    return JsonResponse({"ok": True})


@permission_required("shop.add_coupon")
def create(request):
    Coupon.objects.create(code=request.POST["code"])
    return JsonResponse({"ok": True})


class AccountView(LoginRequiredMixin, View):
    def get(self, request):
        return JsonResponse({"coupons": list(Coupon.objects.values_list("code", flat=True))})


@method_decorator(login_required, name="dispatch")
class ExportView(View):
    def get(self, request):
        return JsonResponse({})


class CouponViewSet(viewsets.ModelViewSet):
    queryset = Coupon.objects.all()
    permission_classes = [permissions.IsAuthenticated]


class PublicStats(APIView):
    def get(self, request):
        return JsonResponse({"n": Coupon.objects.count()})
