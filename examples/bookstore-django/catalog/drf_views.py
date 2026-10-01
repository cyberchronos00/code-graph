from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Review
from .serializers import ReviewSerializer


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer

    @action(detail=True, methods=["post"])
    def upvote(self, request, pk=None):
        review = self.get_object()
        review.upvotes += 1
        review.save()
        return Response({"upvotes": review.upvotes})


class HealthView(APIView):
    def get(self, request):
        return Response({"ok": True})
