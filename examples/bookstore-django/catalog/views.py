from django.http import JsonResponse
from django.views.generic import DetailView

from .models import Book


def featured(request):
    books = Book.objects.order_by("-in_stock")[:5]
    return JsonResponse({"titles": [b.title for b in books]})


class BookDetailView(DetailView):
    model = Book
    template_name = "catalog/book_detail.html"
