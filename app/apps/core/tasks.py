"""Celery entry points for core application orchestration."""

from celery import shared_task

from .daily_digest_scheduler import dispatch_due_daily_digests


@shared_task(name="core.dispatch_due_daily_digests", ignore_result=True)
def dispatch_due_daily_digests_task():
    return dispatch_due_daily_digests()
