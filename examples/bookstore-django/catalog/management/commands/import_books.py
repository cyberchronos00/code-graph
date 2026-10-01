import csv

from django.core.management.base import BaseCommand

from catalog.models import Author, Book


class Command(BaseCommand):
    help = "Import books from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        with open(options["path"]) as fh:
            for row in csv.DictReader(fh):
                author, _ = Author.objects.get_or_create(name=row["author"])
                Book.objects.create(title=row["title"], author=author, price=row["price"])
