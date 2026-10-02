"""DRF router endpoints through APIClient and APITestCase."""
import pytest
from django.urls import reverse, reverse_lazy
from rest_framework.test import APITestCase

from catalog.models import Review


@pytest.mark.django_db
def test_upvote_review(api_client, review):
    response = api_client.post(reverse("review-upvote", args=[review.pk]))
    assert response.json() == {"upvotes": 1}


@pytest.mark.django_db
def test_health(api_client):
    assert api_client.get("/drf/health/").json() == {"ok": True}


class ReviewListTests(APITestCase):
    url = reverse_lazy("review-list")

    def test_list_reviews(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_delete_review(self):
        review = Review.objects.first()
        self.client.delete(reverse("review-detail", kwargs={"pk": review.pk}))
