"""Sanitized, fail-closed provenance for provider requests (SR0.15A)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl, urlparse


SAFE_QUERY_KEYS = {
    "habitaclia": frozenset({"hab", "m2", "pmin", "pmax"}),
    "fotocasa": frozenset({"minRooms", "minPrice", "maxPrice"}),
    "idealista": frozenset(),
}


def _provider(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SAFE_QUERY_KEYS else "unknown"


def safe_query(provider: str, url: str) -> dict[str, str]:
    approved = SAFE_QUERY_KEYS.get(_provider(provider), frozenset())
    pairs = parse_qsl(urlparse(str(url or "")).query, keep_blank_values=True)
    return {key: value for key, value in pairs if key in approved}


def sanitized_location(provider: str, url: str) -> dict[str, Any]:
    parsed = urlparse(str(url or ""))
    return {
        "provider": _provider(provider),
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path or "/",
        "safe_query": safe_query(provider, url),
    }


def request_fingerprint(executed: dict[str, Any]) -> str:
    canonical = {
        "provider": executed.get("provider") or "unknown",
        "host": executed.get("host") or "",
        "path": executed.get("path") or "/",
        "safe_query": dict(sorted((executed.get("safe_query") or {}).items())),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def planned_query(provider: str, url: str, *, operation: Any = None, geography: Any = None) -> dict[str, Any]:
    return {
        "provider": _provider(provider),
        "operation": str(operation or "unknown"),
        "geography": str(geography or "unknown"),
        "safe_query": safe_query(provider, url),
    }


def executed_request(provider: str, url: str) -> dict[str, Any]:
    result = sanitized_location(provider, url)
    result["request_fingerprint"] = request_fingerprint(result)
    return result


def status_class(status: Any) -> str:
    try:
        value = int(status)
    except (TypeError, ValueError):
        return "unknown"
    return f"{value // 100}xx" if 100 <= value <= 599 else "unknown"


def response_provenance(provider: str, requested_url: str, status: Any, final_url: Any) -> dict[str, Any]:
    if not final_url:
        return {
            "status_class": status_class(status), "final_host": "", "final_path": "",
            "final_safe_query": {}, "redirected": "unknown",
        }
    final = sanitized_location(provider, str(final_url))
    requested = sanitized_location(provider, requested_url)
    final.pop("provider", None)
    return {
        "status_class": status_class(status),
        "final_host": final["host"],
        "final_path": final["path"],
        "final_safe_query": final["safe_query"],
        "redirected": (
            (final["host"], final["path"], final["safe_query"])
            != (requested["host"], requested["path"], requested["safe_query"])
        ),
    }


def query_provenance(
    provider: str,
    planned_url: str,
    executed_url: str | None,
    *,
    operation: Any = None,
    geography: Any = None,
    status: Any = None,
    final_url: Any = None,
) -> dict[str, Any]:
    result = {
        "planned": planned_query(
            provider, planned_url, operation=operation, geography=geography,
        ),
        "executed": "unknown",
        "response": {
            "status_class": "unknown", "final_host": "", "final_path": "",
            "final_safe_query": {}, "redirected": "unknown",
        },
    }
    if executed_url:
        result["executed"] = executed_request(provider, executed_url)
        result["response"] = response_provenance(provider, executed_url, status, final_url)
    return result
