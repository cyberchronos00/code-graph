from django.contrib.auth.decorators import login_required
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("coupons", views.CouponViewSet)

urlpatterns = [
    path("redeem/<str:code>/", views.redeem),
    path("reset/", views.reset),
    path("create/", views.create),
    path("account/", views.AccountView.as_view()),
    path("export/", views.ExportView.as_view()),
    path("stats/", views.PublicStats.as_view()),
    path("wrapped/", login_required(views.PublicStats.as_view())),
    path("api/", include(router.urls)),
]
