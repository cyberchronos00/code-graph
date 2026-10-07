import requests


def check_catalog():
    return requests.get("https://catalog.bookstore.example/books", verify=False)
