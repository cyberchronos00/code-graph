from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Review


@receiver(post_save, sender=Review)
def review_saved(sender, instance, created, **kwargs):
    if created:
        from .tasks import recompute_rating

        recompute_rating.apply_async(args=[instance.book_id])
