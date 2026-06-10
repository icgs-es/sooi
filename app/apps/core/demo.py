from datetime import timedelta
from math import ceil

from django.utils import timezone

DEMO_GROUP_NAME = "sooi_demo_14d"
DEMO_DAYS = 14


def get_demo_status(user):
    if not user or not user.is_authenticated:
        return {
            "is_demo": False,
            "active": False,
            "days_total": DEMO_DAYS,
            "days_remaining": 0,
            "ends_at": None,
        }

    is_demo = user.groups.filter(name=DEMO_GROUP_NAME).exists()

    if not is_demo:
        return {
            "is_demo": False,
            "active": False,
            "days_total": DEMO_DAYS,
            "days_remaining": 0,
            "ends_at": None,
        }

    now = timezone.now()
    starts_at = user.date_joined
    ends_at = starts_at + timedelta(days=DEMO_DAYS)
    seconds_remaining = max((ends_at - now).total_seconds(), 0)
    days_remaining = ceil(seconds_remaining / 86400) if seconds_remaining > 0 else 0

    return {
        "is_demo": True,
        "active": now < ends_at,
        "days_total": DEMO_DAYS,
        "days_remaining": days_remaining,
        "ends_at": ends_at,
    }
