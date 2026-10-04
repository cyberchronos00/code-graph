INSTALLED_APPS = ["shop"]
ROOT_URLCONF = "proj.urls"
CELERY_TASK_ROUTES = {"shop.tasks.send_invoice": {"queue": "invoices"}}
