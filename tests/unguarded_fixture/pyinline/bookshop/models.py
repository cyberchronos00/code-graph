from django.db import models


class Order(models.Model):
    title = models.CharField(max_length=200)
