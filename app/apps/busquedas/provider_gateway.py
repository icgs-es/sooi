"""Run-scoped safety primitives for external AI providers.

This module deliberately contains no provider SDK imports and no budget or
customer-account policy.  It is the small internal governance boundary shared
by provider adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class ProviderOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    HARD_FAILURE = "HARD_FAILURE"
    SKIPPED_CIRCUIT = "SKIPPED_CIRCUIT"


class ProviderCircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


@dataclass
class ProviderCircuit:
    provider: str
    state: ProviderCircuitState = ProviderCircuitState.CLOSED
    reason: str | None = None

    def open(self, reason: str) -> None:
        self.state = ProviderCircuitState.OPEN
        self.reason = reason


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    outcome: ProviderOutcome
    value: T | None = None
    reason: str | None = None


def _error_text(exc: Exception) -> str:
    parts = [type(exc).__name__, str(exc)]
    for name in ("code", "status_code", "body", "message", "response"):
        value = getattr(exc, name, None)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower().replace("-", "_")


def classify_provider_exception(exc: Exception) -> ProviderOutcome:
    """Classify generic provider failures without depending on an SDK."""
    text = _error_text(exc)

    # A provider may rename a tool variant while still supporting the same
    # operation. This narrow compatibility case is safe for one bounded
    # alternate-tool attempt; generic unsupported tools are hard failures.
    tool_variant_compatibility = (
        "invalid value: 'web_search'", 'invalid value: "web_search"',
        "unsupported tool variant", "unknown tool type web_search",
    )
    if any(marker in text for marker in tool_variant_compatibility):
        return ProviderOutcome.RETRYABLE_FAILURE

    hard_markers = (
        "insufficient_quota", "credit_balance_exhausted", "credit balance",
        "invalid_api_key", "invalid api key", "api key invalid", "incorrect api key",
        "authenticationerror", "authentication error", "authentication failed",
        "unauthorized", "billing disabled",
        "billing_not_active", "account disabled", "account_deactivated",
        "unsupported model", "model_not_found", "does not have access to model",
        "unsupported tool", "tool_not_supported",
    )
    if any(marker in text for marker in hard_markers):
        return ProviderOutcome.HARD_FAILURE

    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return ProviderOutcome.HARD_FAILURE
    if status in (500, 502, 503, 504):
        return ProviderOutcome.RETRYABLE_FAILURE

    retryable_markers = (
        "timeout", "timed out", "connection reset", "connectionreset",
        "connection error", "network error", "temporary network",
        "temporarily unavailable", "service unavailable", "bad gateway",
        "gateway timeout", "rate limit", "rate_limit", "too many requests",
    )
    if any(marker in text for marker in retryable_markers):
        return ProviderOutcome.RETRYABLE_FAILURE

    # Unknown provider failures are not account-wide evidence. They remain
    # bounded to the call and must not poison the run-scoped circuit.
    return ProviderOutcome.RETRYABLE_FAILURE


def call_provider(
    circuit: ProviderCircuit,
    operation: Callable[[], T],
) -> ProviderResult[T]:
    if circuit.state is ProviderCircuitState.OPEN:
        return ProviderResult(
            ProviderOutcome.SKIPPED_CIRCUIT,
            reason=circuit.reason or "provider_circuit_open",
        )
    try:
        return ProviderResult(ProviderOutcome.SUCCESS, value=operation())
    except Exception as exc:
        outcome = classify_provider_exception(exc)
        reason = f"{type(exc).__name__}: {exc}"
        if outcome is ProviderOutcome.HARD_FAILURE:
            circuit.open(reason)
        return ProviderResult(outcome, reason=reason)
