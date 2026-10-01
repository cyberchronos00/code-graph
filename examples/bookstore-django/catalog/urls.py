from django.urls import path, re_path

from . import views

app_name = "catalog"

urlpatterns = [
    path("featured/", views.featured, name="featured"),
    re_path(r"^books/(?P<pk>[0-9]+)/$", views.BookDetailView.as_view(), name="book-detail"),
]
