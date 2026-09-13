from math import ceil

from django.utils import timezone

from .plans import resolve_entitlement

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

    entitlement = resolve_entitlement(user)
    if not entitlement.trial_active and not entitlement.trial_expired:
        return {
            "is_demo": False,
            "active": False,
            "days_total": DEMO_DAYS,
            "days_remaining": 0,
            "ends_at": None,
        }

    now = timezone.now()
    ends_at = user.profile.trial_end
    seconds_remaining = max((ends_at - now).total_seconds(), 0) if ends_at else 0
    days_remaining = ceil(seconds_remaining / 86400) if seconds_remaining > 0 else 0

    return {
        "is_demo": True,
        "active": entitlement.trial_active,
        "days_total": DEMO_DAYS,
        "days_remaining": days_remaining,
        "ends_at": ends_at,
    }
