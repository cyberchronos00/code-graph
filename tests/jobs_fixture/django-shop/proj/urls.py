from django.urls import path

from shop import views

urlpatterns = [path("orders/", views.place_order)]
