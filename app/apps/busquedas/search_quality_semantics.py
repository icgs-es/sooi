"""Product-level quality evidence for hybrid search results.

This module deliberately evaluates the final, stored candidate evidence.  It
does not fetch, mutate, or reinterpret the search plan.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


_ACCEPTED = frozenset({"verified", "reviewable", "accepted"})
_PROVIDER_FAILURES = frozenset({"HARD_FAILURE", "RETRYABLE_FAILURE"})
_PRE_PROVIDER_MARKERS = (
    "planner_limit", "budget_exhaust", "budget_gate", "pre_provider",
    "controlled_omission", "controlled omission", "mode_limit", "plan_limit",
    "ai_disabled_by_option",
)

AVAILABILITY_CONFIRMED = "CONFIRMED"
AVAILABILITY_UNKNOWN = "UNKNOWN"
AVAILABILITY_UNAVAILABLE = "UNAVAILABLE"

_UNAVAILABLE_REASON_PREFIXES = (
    "availability_unavailable:",
    "ai_probe_unavailable:",
)


def result_actionability(candidate: dict, *, hard_violations=()) -> dict:
    """Project stored candidate evidence into customer/commercial semantics.

    Search intent is deliberately not consulted here.  Callers that own the
    hard-filter context pass the violations already produced by the canonical
    validators; persisted ``hard_violations`` remains supported for probes.
    """
    candidate = candidate if isinstance(candidate, dict) else {}
    violations = tuple(hard_violations or candidate.get("hard_violations") or ())
    classification = str(candidate.get("classification") or "").strip().lower()
    reason = str(candidate.get("reason") or candidate.get("persisted_reason") or "").lower()
    evidence = candidate.get("availability_evidence")
    evidence_state = str(evidence.get("state") or "").lower() if isinstance(evidence, dict) else ""

    unavailable = evidence_state == "unavailable" or any(
        prefix in reason for prefix in _UNAVAILABLE_REASON_PREFIXES
    )
    availability_unknown = evidence_state == "unknown" or "availability_unknown:" in reason
    if unavailable:
        availability_state = AVAILABILITY_UNAVAILABLE
    elif availability_unknown or classification != "verified":
        availability_state = AVAILABILITY_UNKNOWN
    elif evidence_state == "confirmed":
        availability_state = AVAILABILITY_CONFIRMED
    else:
        # ``verified`` is emitted only after the existing positive validation
        # path; this projection does not manufacture provider evidence.
        availability_state = AVAILABILITY_CONFIRMED

    hard_clean = not violations
    actionable = (
        hard_clean
        and classification == "verified"
        and availability_state == AVAILABILITY_CONFIRMED
    )
    review_required = hard_clean and not actionable and not unavailable
    if unavailable:
        label, message = "No disponible", "No disponible"
    elif review_required:
        label = "Para revisar"
        message = (
            "Disponibilidad pendiente de confirmar"
            if availability_state == AVAILABILITY_UNKNOWN else "Para revisar"
        )
    elif actionable:
        label, message = "Verificado", "Verificado"
    else:
        label, message = "Descartado", "Descartado"
    return {
        "hard_clean": hard_clean,
        "review_required": review_required,
        "actionable": actionable,
        "availability_state": availability_state,
        "customer_label": label,
        "customer_message": message,
    }


def _reason_key(reason: str) -> str:
    return str(reason or "").split(":", 1)[0]


def _provider_was_actually_invoked(row: dict[str, Any]) -> bool:
    """Exclude controlled gates even when legacy rows called them failures."""
    if not row.get("attempted"):
        return False
    text = " ".join(str(row.get(key) or "") for key in (
        "provider_reason", "error", "reason", "method",
    )).lower().replace("-", "_")
    return not any(marker in text for marker in _PRE_PROVIDER_MARKERS)


def evaluate_search_quality_semantics(payload: dict) -> dict:
    """Return candidate safety, completeness, and provider evidence separately."""
    payload = payload if isinstance(payload, dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    rows = payload.get("source_coverage")
    if not isinstance(rows, list):
        rows = []

    # Lazy import avoids making the hybrid service depend on itself at import time
    # while ensuring there is one authoritative set of product validators.
    from .services_hybrid_coverage_v261 import (
        _candidate_constraint_unknowns,
        _candidate_constraint_violations,
    )

    candidates = [
        candidate
        for row in rows if isinstance(row, dict)
        for candidate in (row.get("candidates") or [])
        if isinstance(candidate, dict)
    ]
    if not candidates and isinstance(payload.get("candidates"), list):
        candidates = [c for c in payload["candidates"] if isinstance(c, dict)]

    violation_counts: Counter[str] = Counter()
    unknown_counts: Counter[str] = Counter()
    candidates_with_violations = 0
    accepted = 0
    accepted_clean = 0
    accepted_with_violations = 0
    accepted_violation_total = 0
    rejected_by_constraints = 0
    review_required = 0
    actionable = 0
    unavailable = 0
    hard_clean = 0
    urls: set[str] = set()

    for candidate in candidates:
        violations = _candidate_constraint_violations(context, candidate)
        unknowns = _candidate_constraint_unknowns(context, candidate)
        for violation in violations:
            violation_counts[_reason_key(violation)] += 1
        for unknown in unknowns:
            unknown_counts[str(unknown).rsplit(":", 1)[-1]] += 1
        if violations:
            candidates_with_violations += 1
        classification = str(candidate.get("classification") or "").lower()
        projected = result_actionability(candidate, hard_violations=violations)
        hard_clean += int(projected["hard_clean"])
        review_required += int(projected["review_required"])
        actionable += int(projected["actionable"])
        unavailable += int(projected["availability_state"] == AVAILABILITY_UNAVAILABLE)
        is_accepted = classification in _ACCEPTED
        if is_accepted:
            accepted += 1
            if violations:
                accepted_with_violations += 1
                accepted_violation_total += len(violations)
            else:
                accepted_clean += 1
        elif violations:
            rejected_by_constraints += 1
        url = str(candidate.get("url") or candidate.get("source_url") or "").strip()
        if url:
            urls.add(url)

    actual_failures = sum(
        1 for row in rows
        if isinstance(row, dict)
        and str(row.get("provider_outcome") or "").upper() in _PROVIDER_FAILURES
        and _provider_was_actually_invoked(row)
    )
    planner_limited = sum(
        1 for row in rows if isinstance(row, dict) and any(
            marker in " ".join(str(row.get(key) or "") for key in (
                "provider_reason", "error", "reason", "method",
            )).lower().replace("-", "_")
            for marker in _PRE_PROVIDER_MARKERS
        )
    )
    format_noncompliance = sum(
        int((row.get("stage_counters") or {}).get("format_noncompliance_count") or 0)
        for row in rows if isinstance(row, dict)
    )
    raw = len(candidates)
    return {
        "raw_candidates": raw,
        "candidates_with_hard_constraint_violations": candidates_with_violations,
        "rejected_by_hard_constraints": rejected_by_constraints,
        "accepted_candidates": accepted,
        "accepted_clean": accepted_clean,
        "hard_clean_count": hard_clean,
        "review_required_count": review_required,
        "actionable_count": actionable,
        "unavailable_count": unavailable,
        "customer_valid_count": actionable,
        "commercial_yield_pct": round(actionable / raw * 100, 2) if raw else 0.0,
        "accepted_with_hard_constraint_violations": accepted_with_violations,
        "accepted_candidate_safety_passed": accepted_with_violations == 0,
        "hard_constraint_violations_raw": sum(violation_counts.values()),
        "hard_constraint_violations_accepted": accepted_violation_total,
        "hard_constraint_violation_type_counts": dict(sorted(violation_counts.items())),
        "unknown_required_attribute_counts": dict(sorted(unknown_counts.items())),
        "actual_provider_failure_count": actual_failures,
        "planner_limited_count": planner_limited,
        "format_noncompliance_count": format_noncompliance,
        "unique_source_urls": len(urls),
        "acceptance_yield_pct": round(accepted_clean / raw * 100, 2) if raw else 0.0,
    }
