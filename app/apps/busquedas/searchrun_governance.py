"""Durable SearchRun governance primitives for SR0.3A.2.

This module is intentionally free of provider, billing, and planning behavior.
"""

import hashlib
import json
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import SearchRun


GOVERNANCE_VERSION = "1"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def search_governance_enabled() -> bool:
    """Read the narrow rollout flag. Missing/unknown values are safely off."""
    return os.environ.get("SOOI_SEARCH_GOVERNANCE_V1", "0").strip().casefold() in _TRUE_VALUES


def _text(value):
    if value is None:
        return None
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value)).strip()).casefold()


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"Invalid numeric fingerprint value: {value!r}")
    if not number.is_finite():
        raise ValidationError("Fingerprint numeric values must be finite.")
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", "0"} else normalized


def _unordered(values):
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple, set, frozenset)):
        values = [values]
    return sorted(set(filter(None, (_text(value) for value in values))))


def _value(snapshot, *names, default=None):
    for name in names:
        if isinstance(snapshot, dict) and name in snapshot:
            return snapshot[name]
        if not isinstance(snapshot, dict) and hasattr(snapshot, name):
            return getattr(snapshot, name)
    return default


def canonical_fingerprint_payload(snapshot, search_mode: str) -> dict:
    """Return the locality-agnostic canonical search semantics used by the hash."""
    municipalities = _value(snapshot, "municipalities", "search_locations")
    if not isinstance(snapshot, dict) and hasattr(snapshot, "canonical_search_locations"):
        municipalities = snapshot.canonical_search_locations()
    if municipalities in (None, []):
        municipalities = _value(snapshot, "zone", default=[])
        if isinstance(municipalities, str):
            municipalities = municipalities.split(",")
    from .geography_runtime import frozen_identity_tokens
    identity_tokens = frozen_identity_tokens(snapshot) if isinstance(snapshot, dict) else None
    geography = {
        "scope": _text(_value(snapshot, "geography_scope", "location_scope", default="legacy")),
        "province": _text(_value(snapshot, "province")),
        "municipalities": identity_tokens if identity_tokens is not None else _unordered(municipalities),
        "area": _text(_value(snapshot, "geographic_area_id", "geographic_area")),
    }
    if identity_tokens is not None:
        geography.update({
            "runtime_contract": snapshot.get("geography_runtime_contract"),
            "registry_version": snapshot.get("registry_version"),
            "runtime_mode": snapshot.get("runtime_mode"),
        })
    return {
        "operation": _text(_value(snapshot, "operation_type", "operation")),
        "geography": geography,
        "property_types": _unordered(_value(snapshot, "property_types", default=[])),
        "min_price": _number(_value(snapshot, "min_price")),
        "max_price": _number(_value(snapshot, "max_price")),
        "min_bedrooms": _number(_value(snapshot, "min_bedrooms")),
        "min_area": _number(_value(snapshot, "min_area_m2", "min_area")),
        "search_mode": _text(search_mode),
    }


def build_search_fingerprint(snapshot, search_mode: str) -> str:
    if search_mode not in SearchRun.SearchMode.values or search_mode == SearchRun.SearchMode.LEGACY:
        raise ValidationError({"search_mode": "A governed search mode is required."})
    encoded = json.dumps(
        canonical_fingerprint_payload(snapshot, search_mode),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@transaction.atomic
def initialize_search_run_governance(
    run: SearchRun,
    snapshot,
    *,
    search_mode: str,
    budget_max_credits,
    require_feature_flag: bool = True,
) -> bool:
    """Persist an idempotent authorization snapshot; return whether enabled.

    No calls, consumption, ledger writes, or execution topology changes occur.
    """
    if require_feature_flag and not search_governance_enabled():
        return False
    if search_mode not in SearchRun.SearchMode.values or search_mode == SearchRun.SearchMode.LEGACY:
        raise ValidationError({"search_mode": "Unsupported governed search mode."})
    try:
        maximum = Decimal(str(budget_max_credits))
    except (InvalidOperation, ValueError):
        raise ValidationError({"budget_max_credits": "Budget must be numeric."})
    if not maximum.is_finite() or maximum < 0:
        raise ValidationError({"budget_max_credits": "Budget must be finite and non-negative."})

    if run.pk:
        current = SearchRun.objects.select_for_update().get(pk=run.pk)
        # Reflect durable values in case the caller held a stale instance.
        durable_fields = (
            "governance_enabled", "governance_version", "search_mode", "coverage_status",
            "stop_reason", "budget_max_credits", "budget_reserved_credits",
            "budget_consumed_credits", "sources_planned", "sources_executed",
            "sources_omitted", "calls_planned", "calls_attempted", "calls_succeeded",
            "cache_hits", "estimated_internal_cost", "actual_internal_cost",
            "search_fingerprint",
        )
        for field in durable_fields:
            setattr(run, field, getattr(current, field))

    reserved = run.budget_reserved_credits or Decimal("0")
    consumed = run.budget_consumed_credits or Decimal("0")
    if maximum < max(reserved, consumed):
        raise ValidationError({"budget_max_credits": "Budget cannot be below reserved or consumed credits."})

    fingerprint = build_search_fingerprint(snapshot, search_mode)
    first_initialization = not run.governance_enabled
    run.governance_enabled = True
    run.governance_version = GOVERNANCE_VERSION
    run.search_mode = search_mode
    run.budget_max_credits = maximum
    run.budget_reserved_credits = reserved
    run.budget_consumed_credits = consumed
    run.search_fingerprint = fingerprint
    if first_initialization:
        run.coverage_status = SearchRun.CoverageStatus.NOT_EVALUATED
        run.stop_reason = SearchRun.StopReason.NONE
        for field in (
            "sources_planned", "sources_executed", "sources_omitted", "calls_planned",
            "calls_attempted", "calls_succeeded", "cache_hits",
        ):
            setattr(run, field, 0)
        run.estimated_internal_cost = None
        run.actual_internal_cost = None
    run.full_clean()
    run.save(update_fields=[
        "governance_enabled", "governance_version", "search_mode", "coverage_status",
        "stop_reason", "budget_max_credits", "budget_reserved_credits",
        "budget_consumed_credits", "sources_planned", "sources_executed",
        "sources_omitted", "calls_planned", "calls_attempted", "calls_succeeded",
        "cache_hits", "estimated_internal_cost", "actual_internal_cost",
        "search_fingerprint", "updated_at",
    ])
    return True


def project_hard_provider_stop(run: SearchRun, source_coverage) -> bool:
    """Project existing SR0.3A.1 outcomes without changing provider behavior."""
    if not search_governance_enabled() or not run.governance_enabled:
        return False
    if not any(row.get("provider_outcome") == "HARD_FAILURE" for row in source_coverage or []):
        return False
    run.coverage_status = SearchRun.CoverageStatus.DEGRADED_PROVIDER
    run.stop_reason = SearchRun.StopReason.PROVIDER_HARD_FAILURE
    return True
