import os

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "shop",
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "USER": "shop",
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
    },
    "local": {"ENGINE": "django.db.backends.sqlite3", "NAME": "local.sqlite3"},
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "KEY_PREFIX": "shop:",
        "LOCATION": os.environ.get("REDIS_URL", "redis://cache:6379/1"),
    }
}

CELERY_BROKER_URL = "amqp://shop@queue.internal.example:5672/shop"
EMAIL_HOST = "smtp.example.org"
