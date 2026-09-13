"""Lightweight selection and dispatch for due Daily Digest preferences."""

from dataclasses import dataclass
import logging
from zoneinfo import ZoneInfo

from django.db.models import Exists, OuterRef
from django.utils import timezone

from .daily_digest_delivery import deliver_daily_digest
from .models import DailyDigestDelivery, NotificationPreference


logger = logging.getLogger(__name__)
MADRID = ZoneInfo("Europe/Madrid")
PUBLIC_BASE_URL = "https://sooi.io"


@dataclass(frozen=True)
class DailyDigestDispatchSummary:
    local_date: object
    local_time: object
    eligible_count: int = 0
    processed_count: int = 0
    sent_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    unknown_count: int = 0
    processing_count: int = 0
    error_count: int = 0


def _instant(now):
    value = now or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, MADRID)
    return value


def dispatch_due_daily_digests(*, now=None, batch_size=20):
    """Dispatch one bounded batch of due preferences for the Madrid local day."""
    instant = _instant(now)
    madrid_now = timezone.localtime(instant, MADRID)
    local_date = madrid_now.date()
    local_time = madrid_now.time().replace(tzinfo=None)
    limit = max(int(batch_size), 0)

    today_delivery = DailyDigestDelivery.objects.filter(
        owner_id=OuterRef("owner_id"),
        delivery_type=DailyDigestDelivery.DeliveryType.DAILY_DIGEST,
        channel=DailyDigestDelivery.Channel.EMAIL,
        local_date=local_date,
    )
    due_preferences = list(
        NotificationPreference.objects.filter(
            daily_digest_enabled=True,
            daily_digest_time__lte=local_time,
        )
        .annotate(has_today_delivery=Exists(today_delivery))
        .filter(has_today_delivery=False)
        .select_related("owner")
        .order_by("daily_digest_time", "owner_id")[:limit]
    )

    counts = {
        "sent_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "unknown_count": 0,
        "processing_count": 0,
        "error_count": 0,
    }
    for preference in due_preferences:
        try:
            result = deliver_daily_digest(
                preference.owner,
                now=instant,
                base_url=PUBLIC_BASE_URL,
            )
        except Exception as exc:
            counts["error_count"] += 1
            logger.error(
                "daily digest dispatch user failure",
                extra={
                    "component": "daily_digest_scheduler",
                    "exception_class": exc.__class__.__name__,
                    "owner_id": preference.owner_id,
                    "local_date": local_date.isoformat(),
                },
            )
            continue

        counter = {
            DailyDigestDelivery.Status.SENT: "sent_count",
            DailyDigestDelivery.Status.SKIPPED: "skipped_count",
            DailyDigestDelivery.Status.FAILED: "failed_count",
            DailyDigestDelivery.Status.UNKNOWN: "unknown_count",
            DailyDigestDelivery.Status.PROCESSING: "processing_count",
        }.get(result.status)
        if counter:
            counts[counter] += 1
        else:
            counts["error_count"] += 1

    return DailyDigestDispatchSummary(
        local_date=local_date,
        local_time=local_time,
        eligible_count=len(due_preferences),
        processed_count=len(due_preferences),
        **counts,
    )
