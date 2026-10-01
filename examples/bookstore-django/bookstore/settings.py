import os

import environ

env = environ.Env()

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost").split(",")
STORE_CURRENCY = env("STORE_CURRENCY", default="EUR")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "channels",
    "catalog",
]

ROOT_URLCONF = "bookstore.urls"
ASGI_APPLICATION = "bookstore.asgi.application"
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")

DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3")}
