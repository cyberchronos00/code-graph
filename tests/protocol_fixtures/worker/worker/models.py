from django.db import models


class Order(models.Model):
    total = models.IntegerField()
    status = models.CharField(max_length=20)

    class Meta:
        db_table = "orders"


class Shipment(models.Model):
    order_id = models.IntegerField()

    class Meta:
        db_table = "shipments"
