from django.db import models


class Refund(models.Model):
    order_id = models.IntegerField()
    amount = models.IntegerField()

    class Meta:
        db_table = "refunds"
