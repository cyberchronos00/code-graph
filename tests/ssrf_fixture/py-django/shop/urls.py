from django.urls import path

from . import views

urlpatterns = [
    path("cover/", views.cover),
    path("covers/<str:isbn>/", views.by_isbn),
    path("notify/", views.notify),
    path("import/", views.import_feed),
    path("mirror/", views.mirror),
    path("lookup/", views.lookup),
    path("raw/<str:link>/", views.raw),
]
