from django.urls import path

from . import views

urlpatterns = [
    path("orders/", views.create_order),
    path("admin/purge/", views.purge_orders),
    path("health/", views.health),
    path("hooks/github-open/", views.github_open),
    path("hooks/github/", views.github_signed),
]
