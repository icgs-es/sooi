"""Owner-scoped persisted evidence and analyze/write reuse (SR0.3A.4)."""
from __future__ import annotations

import copy
import os
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import SearchRun
from .searchrun_governance import build_search_fingerprint, search_governance_enabled

DEFAULT_REUSE_TTL_SECONDS = 3600


def reuse_enabled() -> bool:
    return search_governance_enabled()


def reuse_ttl():
    try:
        seconds = int(os.environ.get("SOOI_SEARCH_REUSE_TTL_SECONDS", DEFAULT_REUSE_TTL_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_REUSE_TTL_SECONDS
    return timedelta(seconds=max(0, seconds))


def _usable_payload(run):
    payload = run.raw_response if isinstance(run.raw_response, dict) else {}
    rows = payload.get("source_coverage")
    if not isinstance(rows, list):
        return False
    # A provider-hard-failure-only empty response is not market evidence.
    accepted = any(
        c.get("classification") in {"verified", "reviewable"}
        for row in rows for c in (row.get("candidates") or [])
    )
    if run.coverage_status == SearchRun.CoverageStatus.DEGRADED_PROVIDER and not accepted:
        return False
    return True


def find_reusable_run(run: SearchRun, *, now=None):
    if not reuse_enabled() or not run.governance_enabled or not run.search_fingerprint:
        return None
    now = now or timezone.now()
    cutoff = now - reuse_ttl()
    candidates = (
        SearchRun.objects.filter(
            search_profile__owner_id=run.search_profile.owner_id,
            search_fingerprint=run.search_fingerprint,
            search_mode=run.search_mode,
            governance_enabled=True,
            status__in=[SearchRun.Status.COMPLETED, SearchRun.Status.COMPLETED_WITH_ERRORS],
            finished_at__gte=cutoff,
        )
        .exclude(pk=run.pk)
        .exclude(coverage_status__in=[SearchRun.CoverageStatus.FAILED, SearchRun.CoverageStatus.NOT_EVALUATED])
        .order_by("-finished_at", "-pk")
    )
    return next((candidate for candidate in candidates if _usable_payload(candidate)), None)


def reusable_evidence(run: SearchRun):
    candidate = find_reusable_run(run)
    if not candidate or not _usable_payload(candidate):
        return None, []
    rows = copy.deepcopy(candidate.raw_response.get("source_coverage") or [])
    for row in rows:
        row["reused"] = True
        row["reused_from_search_run_id"] = candidate.pk
    run.reused_from_search_run = candidate
    run.cache_hits = (run.cache_hits or 0) + 1
    return candidate, rows


def validate_analysis_run(run_id, owner, fingerprint=None):
    try:
        run = SearchRun.objects.select_related("search_profile").get(pk=run_id)
    except SearchRun.DoesNotExist as exc:
        raise ValidationError("Invalid analysis run.") from exc
    if run.search_profile.owner_id != owner.pk:
        raise PermissionDenied("Analysis belongs to another owner.")
    if not reuse_enabled() or not run.governance_enabled or not _usable_payload(run):
        raise ValidationError("Analysis is unavailable or invalid.")
    if run.status not in {SearchRun.Status.COMPLETED, SearchRun.Status.COMPLETED_WITH_ERRORS}:
        raise ValidationError("Analysis is not complete.")
    if not run.finished_at or run.finished_at < timezone.now() - reuse_ttl():
        raise ValidationError("Analysis has expired.")
    expected = fingerprint or build_search_fingerprint(run.search_profile, run.search_mode)
    if expected != run.search_fingerprint:
        raise ValidationError("Analysis fingerprint does not match.")
    return run


@transaction.atomic
def write_analysis(run_id, owner, fingerprint=None):
    """Persist a stored analysis only; this function has no discovery boundary."""
    run = validate_analysis_run(run_id, owner, fingerprint)
    from .services_hybrid_coverage_v261 import _apply_action_plan_to_db, _build_action_plan
    payload = copy.deepcopy(run.raw_response)
    rows = payload["source_coverage"]
    # Rebuild against current persistence so repeated WRITE is idempotent.
    plan = _build_action_plan(rows)
    result = _apply_action_plan_to_db(
        run.search_profile, payload.get("context") or {}, rows, plan, search_run=run,
    )
    payload["write"] = True
    payload["write_result"] = result
    run.raw_response = payload
    run.total_new = (run.total_new or 0) + result["created"]
    run.total_updated = (run.total_updated or 0) + result["updated"]
    run.save(update_fields=["raw_response", "total_new", "total_updated", "updated_at"])
    return payload
