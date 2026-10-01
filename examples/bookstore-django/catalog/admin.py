from django.contrib import admin

from .models import Author, Book, Order


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "price"]


admin.site.register(Author)
admin.site.register(Order)
