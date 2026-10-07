from django.urls import path

from . import views

urlpatterns = [
    path("orders/", views.order_list),
    path("ledger/", views.ledger),
    path("reports/", views.reports),
    path("catalog/", views.catalog),
    path("sitemap.xml", views.sitemap),
    path("lookup/", views.lookup),
]
