from django.db.models.signals import post_save, pre_delete
from django.dispatch import Signal, receiver

from .models import Order, Shipment

shipment_ready = Signal()


@receiver(post_save, sender=Order)
def order_saved(sender, instance, **kwargs):
    pass


@receiver(pre_delete, sender=Shipment)
def shipment_deleted(sender, instance, **kwargs):
    pass


@receiver(shipment_ready)
def notify_shipment(sender, **kwargs):
    pass
