"""Adaptive, provider-independent source planning for SR0.3A.3.

The numbers below are conservative technical guardrails, not prices, customer
credits, or a billing entitlement.  One AI provider invocation consumes one
planner cost unit.  These defaults are deliberately centralized for later
calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Sequence


MARKET_COMPLETENESS_DISCLAIMER = (
    "El 100% se refiere al plan SOOI mostrado; no garantiza cubrir todo el mercado."
)


def location_batches(locations: Sequence, maximum_calls: int = 4) -> list[list]:
    """Return the existing balanced location topology without provider work."""
    locations = list(locations or ())
    if not locations:
        return []
    call_count = min(len(locations), max(0, int(maximum_calls)))
    if not call_count:
        return []
    batch_size, larger_batches = divmod(len(locations), call_count)
    batches, start = [], 0
    for index in range(call_count):
        end = start + batch_size + (1 if index < larger_batches else 0)
        batches.append(locations[start:end])
        start = end
    return batches


def coverage_plan(locations: Sequence, authorized_credits, *, maximum_calls: int = 4,
                  planned_calls: int | None = None,
                  consolidate_locations: bool = False) -> dict:
    """Materialize the homogeneous canonical-location customer denominator."""
    geography_batches = (
        [list(locations)] if consolidate_locations and locations else
        location_batches(locations, maximum_calls)
    )
    planned_call_count = len(geography_batches) if planned_calls is None else max(0, int(planned_calls))
    batches = [geography_batches[index % len(geography_batches)] for index in range(planned_call_count)] if geography_batches else []
    try:
        authorized = max(0, int(Decimal(str(authorized_credits or 0))))
    except (ValueError, TypeError):
        authorized = 0
    authorized_calls = min(len(batches), authorized)
    scheduled = sum(len(batch) for batch in batches[:authorized_calls])
    planned = sum(len(batch) for batch in batches)
    return {
        "contract_version": "SOOI_SR010_COVERAGE_COST_POLICY_V1",
        "planned_calls": len(batches),
        "authorized_calls": authorized_calls,
        "planned_units": planned,
        "scheduled_units": scheduled,
        "coverage_percent_planned": round(100 * scheduled / planned, 2) if planned else None,
        "authorized_units": scheduled,
        "credits_required_for_full_plan": len(batches),
        "estimated_additional_credits_after_run": max(0, len(batches) - authorized_calls),
        "credits_authorized": authorized,
        "market_completeness_disclaimer": MARKET_COMPLETENESS_DISCLAIMER,
        "batches": [list(batch) for batch in batches],
        "coverage_axes": (
            {"geographic_locations_planned": len(list(locations or ())),
             "source_location_units_planned": planned}
            if planned > len(list(locations or ())) else None
        ),
    }


def classify_coverage_result(plan: dict, *, executed_units: int, accepted_candidates: int,
                             degraded: bool = False, stop_reason: str | None = None) -> dict:
    """Apply the SR0.10 empty-result invariant to already-computed evidence."""
    planned = int(plan.get("planned_units") or 0)
    executed = min(planned, max(0, int(executed_units or 0)))
    remaining = max(0, planned - executed)
    full = bool(planned) and remaining == 0 and not degraded and stop_reason not in {
        "sufficient_coverage", "budget_exhausted", "format_noncompliance",
    }
    return {
        "executed_units": executed,
        "remaining_units": remaining,
        "coverage_percent_executed": round(100 * executed / planned, 2) if planned else None,
        "result_scope": "FULL_SOOI_PLAN" if full else "EXECUTED_SUBSET",
        "empty_result_classification": (
            "FULL_PLAN_EMPTY_RESULT" if accepted_candidates == 0 and full else
            "PARTIAL_EMPTY_RESULT" if accepted_candidates == 0 else "RESULTS_IN_EXECUTED_COVERAGE"
        ),
        "coverage_status": "full" if full else ("degraded_provider" if degraded else "partial"),
        "is_full_plan": full,
    }


class SourceTier(str, Enum):
    LOCAL = "local"
    DETERMINISTIC = "deterministic"
    AI_EFFICIENT = "ai_efficient"
    AI_EXPANSION = "ai_expansion"


@dataclass(frozen=True)
class ModePolicy:
    allowed_tiers: frozenset[SourceTier]
    max_ai_sources: int
    max_ai_calls: int
    sufficient_unique_candidates: int
    sufficient_source_diversity: int = 1


MODE_POLICIES = {
    "free": ModePolicy(frozenset({SourceTier.LOCAL, SourceTier.DETERMINISTIC}), 0, 0, 2),
    "eco": ModePolicy(frozenset({SourceTier.LOCAL, SourceTier.DETERMINISTIC, SourceTier.AI_EFFICIENT}), 2, 4, 3),
    "amplia": ModePolicy(frozenset({SourceTier.LOCAL, SourceTier.DETERMINISTIC, SourceTier.AI_EFFICIENT, SourceTier.AI_EXPANSION}), 5, 12, 6, 2),
    "profunda": ModePolicy(frozenset(SourceTier), 8, 20, 10, 2),
}


def planner_for_run(run, specs: Sequence):
    """Return a planner only for both flag-enabled and governed executions."""
    from .searchrun_governance import search_governance_enabled
    if run is None or not search_governance_enabled() or not getattr(run, "governance_enabled", False):
        return None
    profile = getattr(run, "search_profile", None)
    if profile is not None:
        from .geography_runtime import runtime_search_locations
        locations = runtime_search_locations(profile)
    else:
        locations = []
    if not locations and isinstance(getattr(run, "filters_snapshot", None), dict):
        locations = run.filters_snapshot.get("search_locations") or []
    try:
        from .provider_efficiency_planner import historical_efficiency_for_run
        efficiency_decision = historical_efficiency_for_run(run, specs)
    except Exception:
        # Observability/history must never prevent a search or alter baseline.
        efficiency_decision = None
    return AdaptivePlanner(
        run.search_mode, specs,
        budget_max=run.budget_max_credits,
        budget_reserved=run.budget_reserved_credits,
        budget_consumed=run.budget_consumed_credits,
        calls_attempted=run.calls_attempted,
        calls_succeeded=run.calls_succeeded,
        location_count=max(1, len(locations)) if (profile is not None or hasattr(run, "filters_snapshot")) else 4,
        efficiency_decision=efficiency_decision,
    )


def source_tier(spec) -> SourceTier:
    explicit = getattr(spec, "tier", None)
    if explicit:
        return explicit if isinstance(explicit, SourceTier) else SourceTier(explicit)
    # Adjacent/custom SourceSpecs remain useful in bounded modes without
    # knowing concrete provider slugs; production expansion sources annotate
    # themselves explicitly.
    return SourceTier.DETERMINISTIC if spec.method == "deterministic" else SourceTier.AI_EFFICIENT


def ordered_sources(specs: Iterable, soft_order: Sequence[str] | None = None) -> list:
    rank = {tier: index for index, tier in enumerate(SourceTier)}
    soft_rank = {slug: index for index, slug in enumerate(soft_order or ())}
    return sorted(specs, key=lambda spec: (
        rank[source_tier(spec)],
        soft_rank.get(spec.slug, len(soft_rank) + getattr(spec, "priority", 100)),
        getattr(spec, "priority", 100),
        spec.slug,
    ))


def valid_unique_candidates(rows: Sequence[dict]) -> tuple[int, int]:
    """Count accepted unique listings and contributing sources, never raw rows."""
    seen, sources = set(), set()
    for row in rows:
        for candidate in row.get("candidates") or ():
            if candidate.get("classification") not in {"verified", "reviewable"}:
                continue
            key = candidate.get("source_url") or candidate.get("url")
            if not key:
                # Missing URLs cannot establish cross-source uniqueness.
                continue
            key = str(key).split("#", 1)[0].strip().casefold()
            if key and key not in seen:
                seen.add(key)
                sources.add(row.get("source"))
    return len(seen), len(sources - {None})


def sufficient_coverage(mode: str, rows: Sequence[dict], policy: ModePolicy | None = None) -> bool:
    policy = policy or MODE_POLICIES[mode]
    candidates, diversity = valid_unique_candidates(rows)
    return candidates >= policy.sufficient_unique_candidates and diversity >= policy.sufficient_source_diversity


def actionable_unique_candidates(rows: Sequence[dict]) -> tuple[int, int]:
    """Commercially strong unique results; reviewable/hard-clean alone never suffice."""
    from .search_quality_semantics import result_actionability

    seen, sources = set(), set()
    for row in rows:
        for candidate in row.get("candidates") or ():
            if not result_actionability(candidate).get("actionable"):
                continue
            key = candidate.get("source_url") or candidate.get("url")
            if not key:
                continue
            key = str(key).split("#", 1)[0].strip().casefold()
            if key and key not in seen:
                seen.add(key)
                sources.add(row.get("source"))
    return len(seen), len(sources - {None})


def actionable_sufficient_coverage(
    mode: str, rows: Sequence[dict], policy: ModePolicy | None = None,
) -> bool:
    policy = policy or MODE_POLICIES[mode]
    candidates, diversity = actionable_unique_candidates(rows)
    return (
        candidates >= policy.sufficient_unique_candidates
        and diversity >= policy.sufficient_source_diversity
    )


class AdaptivePlanner:
    """Run-local state machine. Persistence is projected by ``apply_to_run``."""

    CALL_COST_UNITS = Decimal("1")

    def __init__(self, mode: str, specs: Sequence, *, budget_max=None, budget_reserved=None,
                 budget_consumed=None, calls_attempted=0, calls_succeeded=0, location_count=4,
                 policy: ModePolicy | None = None, efficiency_decision: dict | None = None):
        self.mode = mode
        self.policy = policy or MODE_POLICIES[mode]
        self.efficiency_decision = efficiency_decision or {}
        self.specs = ordered_sources(specs, self.efficiency_decision.get("ordered_providers"))
        self.allowed = []
        ai_count = 0
        for spec in self.specs:
            tier = source_tier(spec)
            if tier not in self.policy.allowed_tiers:
                continue
            if tier in {SourceTier.AI_EFFICIENT, SourceTier.AI_EXPANSION}:
                if ai_count >= self.policy.max_ai_sources:
                    continue
                ai_count += 1
            self.allowed.append(spec)
        self.executed_slugs: set[str] = set()
        self.omitted_slugs: set[str] = {s.slug for s in self.specs if s not in self.allowed}
        self.start_calls_attempted = max(0, int(calls_attempted or 0))
        self.start_calls_succeeded = min(
            self.start_calls_attempted, max(0, int(calls_succeeded or 0))
        )
        self.calls_attempted = self.start_calls_attempted
        self.calls_succeeded = self.start_calls_succeeded
        self.stop_reason: str | None = None
        self.provider_degraded = False
        self.budget_max = self._decimal(budget_max)
        self.start_reserved = self._decimal(budget_reserved) or Decimal("0")
        self.start_consumed = self._decimal(budget_consumed) or Decimal("0")
        self.reserved = self.start_reserved
        self.consumed = self.start_consumed
        self.location_count = max(1, int(location_count or 1))
        self.deterministic_observation: dict = {
            "deterministic_actionable_count": 0,
            "deterministic_hard_clean_count": 0,
            "deterministic_review_required_count": 0,
            "ai_call_allowed": self.ai_call_allowed,
            "ai_call_needed": True,
            "decision_reason": "deterministic_coverage_not_observed",
            "actionable_sufficiency_threshold": self.policy.sufficient_unique_candidates,
        }

    @staticmethod
    def _decimal(value):
        return None if value is None else max(Decimal("0"), Decimal(str(value)))

    @property
    def calls_planned(self) -> int:
        if self.mode == "eco" and self.budget_max == Decimal("1"):
            return int(any(
                source_tier(spec) in {SourceTier.AI_EFFICIENT, SourceTier.AI_EXPANSION}
                for spec in self.allowed
            ))
        return min(self.policy.max_ai_calls, sum(
            1 for spec in self.allowed if source_tier(spec) in {SourceTier.AI_EFFICIENT, SourceTier.AI_EXPANSION}
        ) * min(4, self.location_count))

    @property
    def ai_call_allowed(self) -> bool:
        if self.calls_attempted >= self.policy.max_ai_calls:
            return False
        next_consumed = self.consumed + self.CALL_COST_UNITS
        return self.budget_max is None or next_consumed <= self.budget_max

    def observe_deterministic_coverage(self, rows: Sequence[dict]) -> None:
        metrics = [
            row.get("provider_efficiency") or {}
            for row in rows if row.get("method") == "deterministic"
        ]
        self.deterministic_observation = {
            "deterministic_actionable_count": sum(int(row.get("actionable_count") or 0) for row in metrics),
            "deterministic_hard_clean_count": sum(int(row.get("hard_clean_count") or 0) for row in metrics),
            "deterministic_review_required_count": sum(int(row.get("review_required_count") or 0) for row in metrics),
            "ai_call_allowed": self.ai_call_allowed,
            "ai_call_needed": not actionable_sufficient_coverage(self.mode, rows, self.policy),
            "decision_reason": (
                "actionable_sufficiency_reached"
                if actionable_sufficient_coverage(self.mode, rows, self.policy)
                else "actionable_sufficiency_not_reached"
            ),
            "actionable_sufficiency_threshold": self.policy.sufficient_unique_candidates,
        }

    def efficiency_observability(self) -> dict:
        return {
            **self.efficiency_decision,
            **self.deterministic_observation,
            "ai_skip_by_actionable_sufficiency": False,
        }

    def before_external_call(self) -> bool:
        if self.calls_attempted >= self.policy.max_ai_calls:
            self.stop_reason = "budget_exhausted"
            return False
        next_consumed = self.consumed + self.CALL_COST_UNITS
        if self.budget_max is not None and next_consumed > self.budget_max:
            self.stop_reason = "budget_exhausted"
            return False
        self.calls_attempted += 1
        self.consumed = next_consumed
        return True

    def after_external_call(self, succeeded: bool) -> None:
        if succeeded:
            self.calls_succeeded += 1

    def record_executed(self, spec) -> None:
        self.executed_slugs.add(spec.slug)

    def stop(self, reason: str, remaining: Iterable = ()) -> None:
        self.stop_reason = reason
        self.omitted_slugs.update(spec.slug for spec in remaining)

    def apply_to_run(self, run, rows: Sequence[dict]) -> None:
        """Idempotently project this execution's absolute bookkeeping."""
        from .models import SearchRun

        planned = len(self.specs)
        executed = len(self.executed_slugs)
        omitted = max(len(self.omitted_slugs), planned - executed)
        run.sources_planned = max(run.sources_planned or 0, planned)
        run.sources_executed = max(run.sources_executed or 0, executed)
        run.sources_omitted = max(run.sources_omitted or 0, omitted)
        run.calls_planned = max(run.calls_planned or 0, self.calls_planned)
        run.calls_attempted = max(run.calls_attempted or 0, self.calls_attempted)
        run.calls_succeeded = min(run.calls_attempted, max(run.calls_succeeded or 0, self.calls_succeeded))
        # The commercial hold is not call spend.  Preserve it; the consumed
        # fallback only maintains the ledger invariant for legacy/unheld runs.
        run.budget_reserved_credits = max(self.start_reserved, self.consumed)
        run.budget_consumed_credits = max(self.start_consumed, self.consumed)

        valid, _ = valid_unique_candidates(rows)
        reason = self.stop_reason or "completed_plan"
        run.stop_reason = reason
        if self.provider_degraded or reason in {"provider_hard_failure", "provider_outage"}:
            run.coverage_status = SearchRun.CoverageStatus.DEGRADED_PROVIDER
        elif reason == "budget_exhausted":
            run.coverage_status = SearchRun.CoverageStatus.LIMITED_BY_BUDGET
        elif valid == 0:
            run.coverage_status = SearchRun.CoverageStatus.NO_RESULTS
        elif self.omitted_slugs:
            run.coverage_status = SearchRun.CoverageStatus.LIMITED_BY_PLAN
        elif reason == "completed_plan":
            run.coverage_status = SearchRun.CoverageStatus.FULL
        else:
            run.coverage_status = SearchRun.CoverageStatus.PARTIAL
