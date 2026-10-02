"""django-ninja API through the pytest-django `client` fixture."""
import pytest

pytestmark = pytest.mark.django_db


def test_list_books(client, book):
    response = client.get("/api/books/", {"q": "Kin"})
    assert response.status_code == 200


@pytest.mark.parametrize("suffix", ["", "availability/"], ids=["detail", "availability"])
def test_book_endpoints(client, book, suffix):
    response = client.get(f"/api/books/{book.pk}/{suffix}")
    assert response.status_code == 200


def test_order_needs_token(client, book, auth_headers):
    payload = {"customer_email": "a@example.com", "lines": [{"book_id": book.pk, "quantity": 1}]}
    response = client.post("/api/orders/", payload, content_type="application/json", **auth_headers)
    assert response.status_code == 200
