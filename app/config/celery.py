import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "dispatch-due-daily-digests-every-minute": {
        "task": "core.dispatch_due_daily_digests",
        "schedule": 60.0,
        "options": {"expires": 55.0},
    },
}
