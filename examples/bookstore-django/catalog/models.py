from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=200)
    bio = models.TextField(blank=True)


class Book(models.Model):
    FORMATS = [("paperback", "Paperback"), ("hardcover", "Hardcover"), ("ebook", "E-book")]

    title = models.CharField(max_length=300)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    price = models.DecimalField(max_digits=8, decimal_places=2)
    format = models.CharField(max_length=20, choices=FORMATS, default="paperback")
    subtitle = models.CharField(max_length=300, null=True, blank=True)
    in_stock = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "store_books"


class Order(models.Model):
    STATUSES = [("pending", "Pending"), ("paid", "Paid"), ("shipped", "Shipped")]

    customer_email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    book = models.ForeignKey(Book, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()


class Review(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    body = models.TextField()
    upvotes = models.PositiveIntegerField(default=0)
