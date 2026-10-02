from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from catalog.models import Author, Book, Review


@pytest.fixture
def author(db):
    return Author.objects.create(name="Octavia")


@pytest.fixture
def book(author):
    return Book.objects.create(title="Kindred", author=author, price=Decimal("9.90"), in_stock=5)


@pytest.fixture
def review(book):
    return Review.objects.create(book=book, rating=5, body="Great")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_headers():
    return {"HTTP_AUTHORIZATION": "Bearer secret"}
