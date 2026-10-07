DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "bookshop",
        "USER": "shop",
        "PASSWORD": "SURF-PY-DB-PW-7f3a",
        "HOST": "db.bookstore.example",
        "PORT": "5432",
    }
}
CELERY_BROKER_URL = "amqp://shop@queue.bookstore.example:5672//"
CACHE_URL = "redis://cache.bookstore.example:6379/0"
ROOT_URLCONF = "bookshop.urls"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "bookshop"]
