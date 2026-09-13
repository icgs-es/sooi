"""Conservative historical soft ranking for SR0.15D."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, Iterable, Sequence


MINIMUM_HISTORY_SAMPLES = 5
MINIMUM_HISTORY_RUNS = 3
MINIMUM_HISTORY_DAYS = 2
HISTORY_WINDOW_DAYS = 90
_FAILURES = frozenset({"HARD_FAILURE", "RETRYABLE_FAILURE"})


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def history_sample_from_row(row: dict[str, Any], *, run_id: Any, finished_at: datetime) -> dict[str, Any] | None:
    """Normalize one actually executed source row; legacy/invalid rows are absent, never zero."""
    if not isinstance(row, dict) or not row.get("attempted") or row.get("reused"):
        return None
    metrics = row.get("provider_efficiency")
    provider = str(row.get("source") or "").strip().lower()
    if not provider or not isinstance(metrics, dict) or not isinstance(finished_at, datetime):
        return None
    values = {
        key: _number(metrics.get(key))
        for key in (
            "candidate_count", "hard_clean_count", "actionable_count",
            "review_required_count", "hard_rejected_count",
        )
    }
    if any(value is None for value in values.values()):
        return None
    candidate_count = values["candidate_count"]
    if (
        values["hard_clean_count"] > candidate_count
        or values["actionable_count"] > candidate_count
        or values["review_required_count"] > candidate_count
        or values["hard_rejected_count"] > candidate_count
    ):
        return None
    return {
        "provider": provider,
        "run_id": run_id,
        "finished_at": finished_at,
        **values,
        "failed": str(row.get("provider_outcome") or "").upper() in _FAILURES,
    }


def _eligible(samples: Sequence[dict[str, Any]]) -> bool:
    return (
        len(samples) >= MINIMUM_HISTORY_SAMPLES
        and len({sample["run_id"] for sample in samples}) >= MINIMUM_HISTORY_RUNS
        and len({sample["finished_at"].date() for sample in samples}) >= MINIMUM_HISTORY_DAYS
    )


def _score(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    candidates = sum(sample["candidate_count"] for sample in samples)
    hard_clean = sum(sample["hard_clean_count"] for sample in samples)
    actionable = sum(sample["actionable_count"] for sample in samples)
    failures = sum(bool(sample["failed"]) for sample in samples)
    return {
        "commercial_yield_pct": round(actionable * 100 / candidates, 4) if candidates else 0.0,
        "provider_yield_pct": round(hard_clean * 100 / candidates, 4) if candidates else 0.0,
        "failure_pct": round(failures * 100 / len(samples), 4),
    }


def _rank_key(score: dict[str, Any]) -> tuple[float, float, float]:
    # Commercial evidence dominates; hard-clean breaks ties; failures penalize.
    return (
        score["commercial_yield_pct"],
        score["provider_yield_pct"],
        -score["failure_pct"],
    )


def provider_efficiency_score(
    samples: Iterable[dict[str, Any]], *, baseline_order: Sequence[str],
    provider_tiers: dict[str, str], now: datetime | None = None,
) -> dict[str, Any]:
    """Return reversible within-tier ordering, or exact baseline on insufficient history."""
    now = now or datetime.now(datetime_timezone.utc)
    cutoff = now - timedelta(days=HISTORY_WINDOW_DAYS)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        finished = sample.get("finished_at") if isinstance(sample, dict) else None
        if not isinstance(finished, datetime):
            continue
        comparable_now = now
        comparable_cutoff = cutoff
        comparable_finished = finished
        if comparable_now.tzinfo and not comparable_finished.tzinfo:
            comparable_finished = comparable_finished.replace(tzinfo=datetime_timezone.utc)
        elif not comparable_now.tzinfo and comparable_finished.tzinfo:
            comparable_finished = comparable_finished.replace(tzinfo=None)
            comparable_cutoff = comparable_cutoff.replace(tzinfo=None)
        if comparable_finished < comparable_cutoff or comparable_finished > comparable_now:
            continue
        provider = str(sample.get("provider") or "").lower()
        if provider in provider_tiers:
            grouped[provider].append(sample)

    diagnostics = {}
    scores = {}
    for provider in baseline_order:
        rows = grouped.get(provider, [])
        eligible = (
            provider_tiers.get(provider) in {"ai_efficient", "ai_expansion"}
            and _eligible(rows)
        )
        score = _score(rows) if rows else {
            "commercial_yield_pct": 0.0, "provider_yield_pct": 0.0, "failure_pct": 0.0,
        }
        diagnostics[provider] = {
            "samples": len(rows),
            "runs": len({row["run_id"] for row in rows}),
            "days": len({row["finished_at"].date() for row in rows}),
            "eligible": eligible,
            "priority": "normal",
            **score,
        }
        if eligible:
            scores[provider] = score

    ordered = list(baseline_order)
    scoring_active = False
    tiers = []
    for provider in baseline_order:
        tier = provider_tiers.get(provider)
        if tier not in tiers:
            tiers.append(tier)
    for tier in tiers:
        positions = [index for index, provider in enumerate(ordered) if provider_tiers.get(provider) == tier]
        eligible_positions = [index for index in positions if ordered[index] in scores]
        if len(eligible_positions) < 2:
            continue
        providers = [ordered[index] for index in eligible_positions]
        ranked = sorted(
            providers,
            key=lambda provider: (_rank_key(scores[provider]), -baseline_order.index(provider)),
            reverse=True,
        )
        for index, provider in zip(eligible_positions, ranked):
            ordered[index] = provider
        scoring_active = True
        keys = [_rank_key(scores[provider]) for provider in ranked]
        if keys[0] != keys[-1]:
            diagnostics[ranked[0]]["priority"] = "high"
            diagnostics[ranked[-1]]["priority"] = "low"

    return {
        "scoring_mode": "historical_soft_priority" if scoring_active else "insufficient_history",
        "fail_open": not scoring_active,
        "history_samples": sum(len(rows) for rows in grouped.values()),
        "minimum_history_required": {
            "samples_per_provider": MINIMUM_HISTORY_SAMPLES,
            "runs": MINIMUM_HISTORY_RUNS,
            "days": MINIMUM_HISTORY_DAYS,
            "window_days": HISTORY_WINDOW_DAYS,
        },
        "ordered_providers": ordered,
        "provider_priorities": [
            {"provider": provider, **diagnostics[provider]} for provider in baseline_order
        ],
        "provider_disabled": [],
    }


def _comparison_signature(snapshot: Any) -> tuple:
    value = snapshot if isinstance(snapshot, dict) else {}
    return (
        str(value.get("operation") or value.get("operation_type") or "").lower(),
        str(value.get("province") or "").casefold(),
        str(value.get("location_scope") or value.get("geography_scope") or "").lower(),
        tuple(sorted(str(item).lower() for item in (value.get("property_types") or []) if item)),
        str(value.get("min_price") or ""), str(value.get("max_price") or ""),
        str(value.get("bedrooms") or value.get("min_bedrooms") or ""),
        str(value.get("min_area") or value.get("min_area_m2") or ""),
    )


def historical_efficiency_for_run(run, specs: Sequence, *, now=None) -> dict[str, Any]:
    """Owner-scoped ORM adapter. Any unavailable history fails open."""
    from django.utils import timezone
    from .adaptive_planner import ordered_sources, source_tier

    now = now or timezone.now()
    baseline_specs = ordered_sources(specs)
    baseline = [spec.slug for spec in baseline_specs]
    tiers = {spec.slug: source_tier(spec).value for spec in baseline_specs}
    owner_id = getattr(getattr(run, "search_profile", None), "owner_id", None)
    if not owner_id:
        return provider_efficiency_score([], baseline_order=baseline, provider_tiers=tiers, now=now)
    signature = _comparison_signature(getattr(run, "filters_snapshot", None))
    if not signature[0] or not signature[1]:
        return provider_efficiency_score([], baseline_order=baseline, provider_tiers=tiers, now=now)
    cutoff = now - timedelta(days=HISTORY_WINDOW_DAYS)
    candidates = (
        type(run).objects.filter(
            search_profile__owner_id=owner_id,
            governance_enabled=True,
            search_mode=run.search_mode,
            status__in=["completed", "completed_with_errors"],
            finished_at__gte=cutoff,
        )
        .exclude(pk=run.pk)
        .order_by("-finished_at")[:200]
    )
    samples = []
    for historical_run in candidates:
        if _comparison_signature(historical_run.filters_snapshot) != signature:
            continue
        payload = historical_run.raw_response if isinstance(historical_run.raw_response, dict) else {}
        for row in payload.get("source_coverage") or []:
            sample = history_sample_from_row(
                row, run_id=historical_run.pk, finished_at=historical_run.finished_at,
            )
            if sample:
                samples.append(sample)
    return provider_efficiency_score(
        samples, baseline_order=baseline, provider_tiers=tiers, now=now,
    )
