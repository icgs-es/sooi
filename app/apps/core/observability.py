"""Privacy-safe technical observability for SOOI.

Only fields in ``SAFE_FIELDS`` are emitted. Values are scalar technical
identifiers or bounded enumerations; free text is deliberately rejected.
"""
import json
import logging
import re
import uuid

from django.db import connections
from django.http import JsonResponse


LOGGER = logging.getLogger("sooi.operations")
CORRELATION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SAFE_FIELDS = frozenset({
    "event", "status", "correlation_id", "component", "operation",
    "object_type", "object_id", "owner_id", "attempt", "count",
    "duration_ms", "http_status", "provider_state", "reason_code",
})
SAFE_STATUSES = frozenset({"success", "failure", "partial", "retry"})
SAFE_VALUES = {
    "event": frozenset({"capture_conversion", "imap_message", "inbox_conversion", "inbox_discard", "inbox_sync", "readiness", "search_run", "smtp_delivery"}),
    "component": frozenset({"celery", "conversion", "database", "imap", "inbox", "smtp"}),
    "operation": frozenset({"convert", "demo_notice", "discard", "resolve_owner", "run_search", "sync", "welcome"}),
    "object_type": frozenset({"captured_property", "email_account", "inbound_email", "search_profile", "search_run"}),
    "provider_state": frozenset({"completed", "completed_with_errors", "failed", "pending", "running"}),
    "reason_code": frozenset({"accepted", "account_incomplete", "created", "cross_owner_alias", "idempotent_replay", "imap_secret_unavailable", "not_accepted", "not_found", "provider_error", "provider_unavailable", "retry_exhausted", "unexpected_error"}),
}
INTEGER_FIELDS = frozenset({"object_id", "owner_id", "attempt", "count", "duration_ms", "http_status"})


def new_correlation_id(value=None):
    candidate = str(value or "")
    if CORRELATION_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def emit(event, status, correlation_id=None, **fields):
    """Emit a JSON event while refusing unknown fields and invalid states."""
    if status not in SAFE_STATUSES:
        raise ValueError("unsupported observability status")
    unknown = set(fields) - (SAFE_FIELDS - {"event", "status", "correlation_id"})
    if unknown:
        raise ValueError("unsafe observability fields: " + ", ".join(sorted(unknown)))
    if event not in SAFE_VALUES["event"]:
        raise ValueError("unsupported observability event")
    payload = {
        "event": str(event)[:80],
        "status": status,
        "correlation_id": new_correlation_id(correlation_id),
    }
    for key, value in fields.items():
        if value is None:
            continue
        if key in SAFE_VALUES:
            if not isinstance(value, str) or value not in SAFE_VALUES[key]:
                raise ValueError(f"unsupported observability value for {key}")
        elif key in INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"unsupported observability value for {key}")
        payload[key] = value
    LOGGER.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return payload


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.correlation_id = new_correlation_id(request.headers.get("X-Correlation-ID"))
        response = self.get_response(request)
        response["X-Correlation-ID"] = request.correlation_id
        return response


def health(request):
    return JsonResponse({"status": "ok", "component": "web"})


def readiness(request):
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        emit("readiness", "failure", getattr(request, "correlation_id", None), component="database")
        return JsonResponse({"status": "unavailable", "component": "database"}, status=503)
    return JsonResponse({"status": "ready", "component": "web"})
