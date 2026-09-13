"""Governed, idempotent delivery service for one daily digest email."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .email_transport import send_transactional_email
from .models import DailyDigestDelivery, NotificationPreference
from .proactive_workflow import render_daily_digest


MADRID = ZoneInfo("Europe/Madrid")
LEASE_DURATION = timedelta(minutes=5)


@dataclass(frozen=True)
class DailyDigestDeliveryResult:
    status: str
    delivery_id: int | None
    local_date: object
    reason_code: str
    transport_attempted: bool = False
    sent: bool = False
    idempotent: bool = False
    payload_fingerprint: str = ""


def _instant(now):
    value = now or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, MADRID)
    return value


def _result(delivery, *, idempotent=False, transport_attempted=False, sent=False):
    return DailyDigestDeliveryResult(
        status=delivery.status,
        delivery_id=delivery.pk,
        local_date=delivery.local_date,
        reason_code=delivery.reason_code,
        transport_attempted=transport_attempted,
        sent=sent,
        idempotent=idempotent,
        payload_fingerprint=delivery.payload_fingerprint,
    )


def _identity(user, local_date):
    return {
        "owner": user,
        "delivery_type": DailyDigestDelivery.DeliveryType.DAILY_DIGEST,
        "channel": DailyDigestDelivery.Channel.EMAIL,
        "local_date": local_date,
    }


def _fingerprint(*, local_date, recipient, subject, text_body, html_body):
    payload = {
        "channel": DailyDigestDelivery.Channel.EMAIL,
        "delivery_type": DailyDigestDelivery.DeliveryType.DAILY_DIGEST,
        "html_body": html_body,
        "local_date": local_date.isoformat(),
        "recipient": recipient,
        "subject": subject,
        "text_body": text_body,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _record_skip(user, local_date, reason_code, now):
    with transaction.atomic():
        delivery, created = DailyDigestDelivery.objects.select_for_update().get_or_create(
            **_identity(user, local_date),
            defaults={"status": DailyDigestDelivery.Status.SKIPPED, "reason_code": reason_code},
        )
        if created:
            return _result(delivery)
        return _existing_result(delivery, now)


def _existing_result(delivery, now):
    if delivery.status == DailyDigestDelivery.Status.PROCESSING:
        if delivery.lease_expires_at and delivery.lease_expires_at <= now:
            delivery.status = DailyDigestDelivery.Status.UNKNOWN
            delivery.reason_code = "stale_processing_ambiguous"
            delivery.lease_expires_at = None
            delivery.save(update_fields=["status", "reason_code", "lease_expires_at", "updated_at"])
            return _result(delivery, idempotent=True)
        return DailyDigestDeliveryResult(
            status=delivery.status,
            delivery_id=delivery.pk,
            local_date=delivery.local_date,
            reason_code="already_processing",
            idempotent=True,
            payload_fingerprint=delivery.payload_fingerprint,
        )
    return _result(delivery, idempotent=True, sent=delivery.status == DailyDigestDelivery.Status.SENT)


def deliver_daily_digest(user, *, now=None, base_url=None):
    """Deliver today's opted-in actionable digest at most once, conservatively."""
    attempt_time = _instant(now)
    local_date = timezone.localtime(attempt_time, MADRID).date()

    if not NotificationPreference.objects.filter(
        owner=user, daily_digest_enabled=True
    ).exists():
        return DailyDigestDeliveryResult(
            status="not_eligible",
            delivery_id=None,
            local_date=local_date,
            reason_code="explicit_opt_in_required",
            idempotent=True,
        )

    recipient = (user.email or "").strip()
    if not recipient:
        return _record_skip(user, local_date, "missing_recipient", attempt_time)

    try:
        rendered = render_daily_digest(user, now=attempt_time, base_url=base_url)
    except Exception as exc:
        with transaction.atomic():
            delivery, created = DailyDigestDelivery.objects.select_for_update().get_or_create(
                **_identity(user, local_date),
                defaults={
                    "status": DailyDigestDelivery.Status.FAILED,
                    "reason_code": "render_failed",
                    "last_error_class": exc.__class__.__name__,
                },
            )
            if not created:
                return _existing_result(delivery, attempt_time)
            return _result(delivery)

    if rendered["digest"]["total_attention"] == 0:
        return _record_skip(user, local_date, "empty_workflow", attempt_time)

    fingerprint = _fingerprint(
        local_date=local_date,
        recipient=recipient,
        subject=rendered["subject"],
        text_body=rendered["text_body"],
        html_body=rendered["html_body"],
    )
    claim_token = uuid.uuid4()

    with transaction.atomic():
        delivery, created = DailyDigestDelivery.objects.select_for_update().get_or_create(
            **_identity(user, local_date),
            defaults={"status": DailyDigestDelivery.Status.SCHEDULED},
        )
        if not created and delivery.status != DailyDigestDelivery.Status.SCHEDULED:
            return _existing_result(delivery, attempt_time)
        delivery.status = DailyDigestDelivery.Status.PROCESSING
        delivery.reason_code = "processing"
        delivery.last_error_class = ""
        delivery.attempted_at = attempt_time
        delivery.attempt_count += 1
        delivery.payload_fingerprint = fingerprint
        delivery.claim_token = claim_token
        delivery.lease_expires_at = attempt_time + LEASE_DURATION
        delivery.save(update_fields=[
            "status", "reason_code", "last_error_class", "attempted_at", "attempt_count",
            "payload_fingerprint", "claim_token", "lease_expires_at", "updated_at",
        ])

    try:
        sent_count = send_transactional_email(
            subject=rendered["subject"],
            text_body=rendered["text_body"],
            html_body=rendered["html_body"],
            recipient=recipient,
        )
    except Exception as exc:
        with transaction.atomic():
            DailyDigestDelivery.objects.filter(
                pk=delivery.pk,
                status=DailyDigestDelivery.Status.PROCESSING,
                claim_token=claim_token,
            ).update(
                status=DailyDigestDelivery.Status.UNKNOWN,
                reason_code="transport_outcome_ambiguous",
                last_error_class=exc.__class__.__name__,
                lease_expires_at=None,
            )
            delivery.refresh_from_db()
        return _result(delivery, transport_attempted=True)

    final_status = (
        DailyDigestDelivery.Status.SENT if sent_count else DailyDigestDelivery.Status.UNKNOWN
    )
    reason_code = "sent" if sent_count else "transport_zero_ambiguous"
    with transaction.atomic():
        updates = {
            "status": final_status,
            "reason_code": reason_code,
            "last_error_class": "",
            "lease_expires_at": None,
        }
        if sent_count:
            updates["sent_at"] = _instant(now)
        DailyDigestDelivery.objects.filter(
            pk=delivery.pk,
            status=DailyDigestDelivery.Status.PROCESSING,
            claim_token=claim_token,
        ).update(**updates)
        delivery.refresh_from_db()
    return _result(
        delivery,
        transport_attempted=True,
        sent=delivery.status == DailyDigestDelivery.Status.SENT,
    )
