"""SR0.12D geography runtime: opt-in, immutable and COMPAT-only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from .geography_registry import (
    GeographyType, ResolutionStatus, load_authority_registry,
    normalize_geography_input, resolve,
)

GEOGRAPHY_RUNTIME_CONTRACT = "SOOI_GEOGRAPHY_RUNTIME_V1"
GEOGRAPHY_RUNTIME_MODE = "COMPAT"
GEOGRAPHY_ENGINE_SCOPE = "SPAIN_GENERIC"
MALAGA_SPECIAL_CASE_LOGIC = 0
PROVINCE_SPECIFIC_RUNTIME_BRANCHES = 0
_TRUE_VALUES = {"1", "true", "yes", "on"}
_RESOLVED = {ResolutionStatus.EXACT, ResolutionStatus.ALIAS_RESOLVED}
_TYPE_LABELS = {
    "province": "Provincia", "municipality": "Municipio", "district": "Distrito",
    "neighborhood": "Barrio", "comarca": "Comarca", "named_area": "Zona",
}


def geography_runtime_enabled() -> bool:
    return os.environ.get("SOOI_GEOGRAPHY_RUNTIME_V1", "0").strip().casefold() in _TRUE_VALUES


@dataclass(frozen=True, slots=True)
class ResolvedSearchUnit:
    raw_value: str
    canonical_key: str | None
    canonical_name: str | None
    canonical_type: str | None
    resolution_status: str
    registry_version: str
    query_label: str
    compatibility_key: str
    province: str | None
    autonomous_community: str | None
    parent: str | None
    province_key: str | None
    parent_key: str | None
    provenance: dict[str, Any]
    legacy_fallback: bool
    matched_alias: str | None = None
    territorial_label: str | None = None

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "raw_location": self.raw_value,
            "resolution_status": self.resolution_status,
            "canonical_key": self.canonical_key,
            "canonical_name": self.canonical_name,
            "canonical_type": self.canonical_type,
            "province_key": self.province_key,
            "autonomous_community": self.autonomous_community,
            "parent_key": self.parent_key,
            "matched_alias": self.matched_alias,
            "legacy_fallback": self.legacy_fallback,
            "query_label": self.query_label,
            "compatibility_key": self.compatibility_key,
            "provenance": self.provenance,
            "registry_version": self.registry_version,
            "territorial_label": self.territorial_label,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSearchGeography:
    scope: str
    units: tuple[ResolvedSearchUnit, ...]
    registry_version: str
    runtime_contract_version: str = GEOGRAPHY_RUNTIME_CONTRACT
    mode: str = GEOGRAPHY_RUNTIME_MODE

    @property
    def query_labels(self) -> list[str]:
        """Canonical labels used for geography context."""
        return [unit.query_label for unit in self.units]

    @property
    def transport_labels(self) -> list[str]:
        """Compatibility labels used by existing search/provider flows."""
        labels = []

        for unit in self.units:
            if (
                unit.resolution_status
                == ResolutionStatus.ALIAS_RESOLVED.value
                and unit.canonical_name
            ):
                labels.append(
                    unit.canonical_name
                )
            else:
                labels.append(
                    unit.raw_value
                )

        return labels

    def to_snapshot(self) -> dict[str, Any]:
        registry = load_authority_registry()
        coverage = [
            {
                "raw": unit.raw_value, "canonical": unit.canonical_name,
                "status": unit.resolution_status, "type": unit.canonical_type,
                "registry_version": unit.registry_version,
                "legacy_fallback": unit.legacy_fallback,
                "identity_key": unit.canonical_key or unit.compatibility_key,
                "query_label": unit.query_label,
            }
            for unit in self.units
        ]
        return {
            "geography_runtime_contract": self.runtime_contract_version,
            "registry_version": self.registry_version,
            "registry_content_digest": registry.content_digest,
            "runtime_mode": self.mode,
            "intent_resolutions": [unit.to_snapshot() for unit in self.units],
            "coverage_units": coverage,
        }


def resolve_search_geography(
    raw_locations: Iterable[object], *, scope: str = "legacy", province: object = None,
    registry=None,
) -> ResolvedSearchGeography:
    if registry is None:
        registry = load_authority_registry()
    expected = (
        (GeographyType.MUNICIPALITY, GeographyType.DISTRICT)
        if scope in {"municipality", "multi_municipality", "legacy", "multi_location"}
        else None
    )
    units, seen = [], set()
    for value in raw_locations or ():
        raw = str(value or "").strip()
        if not raw:
            continue
        result = resolve(raw, province_hint=province, expected_type=expected, registry=registry)
        identity = registry.identities.get(result.canonical_key) if result.canonical_key else None
        is_resolved = result.status in _RESOLVED
        compatibility_key = "legacy:" + normalize_geography_input(raw)
        dedupe_key = result.canonical_key if is_resolved else compatibility_key
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        units.append(ResolvedSearchUnit(
            raw_value=raw,
            canonical_key=result.canonical_key,
            canonical_name=result.canonical_name,
            canonical_type=result.canonical_type.value if result.canonical_type else None,
            resolution_status=result.status.value,
            registry_version=result.registry_version,
            query_label=(identity.query_label if is_resolved and identity and identity.query_label else (result.canonical_name if is_resolved else raw)),
            compatibility_key=compatibility_key,
            province=(registry.identities[identity.province].canonical_name
                      if identity and identity.province in registry.identities else None),
            autonomous_community=(registry.identities[identity.autonomous_community_key].canonical_name
                                  if identity and identity.autonomous_community_key in registry.identities else None),
            parent=(registry.identities[identity.parent].canonical_name
                    if identity and identity.parent in registry.identities else None),
            province_key=identity.province_key if identity else None,
            parent_key=identity.parent if identity else None,
            provenance=result.provenance.to_dict(),
            legacy_fallback=not is_resolved,
            matched_alias=result.matched_alias,
            territorial_label=(
                f"{_TYPE_LABELS[identity.type.value]} de {registry.identities[identity.parent].canonical_name}"
                if identity and identity.parent and identity.parent in registry.identities else
                (_TYPE_LABELS[identity.type.value] if identity else None)
            ),
        ))
    return ResolvedSearchGeography(scope=scope or "legacy", units=tuple(units), registry_version=registry.registry_version)


def resolve_profile_geography(profile) -> ResolvedSearchGeography:
    scope = getattr(profile, "geography_scope", "") or "legacy"
    return resolve_search_geography(
        profile.canonical_search_locations(), scope=scope,
        province=getattr(profile, "province", None),
    )


def runtime_search_locations(profile) -> list[str]:
    raw = list(profile.canonical_search_locations())

    if not geography_runtime_enabled():
        return raw

    return resolve_profile_geography(
        profile
    ).transport_labels


def geography_snapshot_for_profile(profile) -> dict[str, Any]:
    return resolve_profile_geography(profile).to_snapshot() if geography_runtime_enabled() else {}


def frozen_identity_tokens(snapshot: dict[str, Any]) -> list[str] | None:
    """Return frozen fingerprint identities, or None for historical snapshots."""
    if snapshot.get("geography_runtime_contract") != GEOGRAPHY_RUNTIME_CONTRACT:
        return None
    return sorted({
        row.get("canonical_key") or row.get("compatibility_key")
        for row in snapshot.get("intent_resolutions") or []
        if row.get("canonical_key") or row.get("compatibility_key")
    })


def candidate_location_match(intent_rows: list[dict[str, Any]], candidate_value: object) -> str:
    """Compare candidate evidence only; callers must pass an evidence field."""
    raw = str(candidate_value or "").strip()
    if not raw:
        return "UNKNOWN"
    resolved_intent = [row for row in intent_rows if row.get("resolution_status") in {"EXACT", "ALIAS_RESOLVED"}]
    if not resolved_intent:
        return "LEGACY"
    province_hint = None
    registry = load_authority_registry()

    candidate = resolve(
        raw,
        province_hint=province_hint,
        expected_type=(
            GeographyType.MUNICIPALITY,
            GeographyType.DISTRICT,
        ),
        registry=registry,
    )

    if (
        candidate.status not in _RESOLVED
        or not candidate.canonical_key
    ):
        return "UNKNOWN"
    evidence = registry.identities[candidate.canonical_key]
    parent_only = False
    for row in resolved_intent:
        key = row.get("canonical_key")
        if key == evidence.canonical_key:
            return "MATCH"
        intent = registry.identities.get(key)
        if intent and intent.type is GeographyType.MUNICIPALITY and evidence.parent == intent.canonical_key:
            return "MATCH"
        if intent and intent.type is GeographyType.DISTRICT and intent.parent == evidence.canonical_key:
            parent_only = True
    return "UNKNOWN" if parent_only else "MISMATCH"
