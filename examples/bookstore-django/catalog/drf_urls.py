from django.urls import path
from rest_framework.routers import DefaultRouter

from .drf_views import HealthView, ReviewViewSet

router = DefaultRouter()
router.register(r"reviews", ReviewViewSet, basename="review")

urlpatterns = [path("health/", HealthView.as_view())] + router.urls
