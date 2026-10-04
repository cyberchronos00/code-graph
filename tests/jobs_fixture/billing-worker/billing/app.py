from celery import Celery

app = Celery("billing", broker="redis://localhost:6379/0")
app.conf.task_routes = {
    "billing.export.*": {"queue": "exports"},
}
