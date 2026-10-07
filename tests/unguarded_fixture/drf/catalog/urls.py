from rest_framework.routers import DefaultRouter

from .views import FlyerViewSet, ShelfViewSet, WindowDisplayViewSet

router = DefaultRouter()
router.register("shelf", ShelfViewSet, basename="shelf")
router.register("window", WindowDisplayViewSet, basename="window")
router.register("flyers", FlyerViewSet, basename="flyers")
urlpatterns = router.urls
