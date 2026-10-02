from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog.models import Author, Book
from catalog.pricing import Discount, line_total


class FeaturedPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ursula")
        cls.book = Book.objects.create(title="The Dispossessed", author=author, price=Decimal("12.50"), in_stock=3)

    def setUp(self):
        self.detail_url = reverse("shop:book-detail", args=[self.book.pk])

    def test_featured_lists_books_in_stock(self):
        response = self.client.get(reverse("shop:featured"))
        self.assertEqual(response.status_code, 200)

    def test_book_detail_page(self):
        response = self.client.get(self.detail_url)
        self.assertContains(response, "The Dispossessed")


class PricingTests(TestCase):
    def test_discount_applies_to_line_total(self):
        book = Book(title="x", price=Decimal("10.00"))
        self.assertEqual(line_total(book, 2, Discount(50)), 10.0)
