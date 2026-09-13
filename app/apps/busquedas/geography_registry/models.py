"""Immutable value objects for the geography registry foundation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class GeographyType(str, Enum):
    COUNTRY = "country"
    AUTONOMOUS_COMMUNITY = "autonomous_community"
    AUTONOMOUS_CITY = "autonomous_city"
    PROVINCE = "province"
    MUNICIPALITY = "municipality"
    ISLAND = "island"
    TERRITORIAL_AREA = "territorial_area"
    DISTRICT = "district"
    NEIGHBORHOOD = "neighborhood"
    COMARCA = "comarca"
    NAMED_AREA = "named_area"


class ResolutionStatus(str, Enum):
    EXACT = "EXACT"
    ALIAS_RESOLVED = "ALIAS_RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    OUTSIDE_PROVINCE = "OUTSIDE_PROVINCE"
    INVALID_TYPE = "INVALID_TYPE"


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    provenance_id: str
    kind: str
    reference: str
    note: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provenance_id": self.provenance_id,
            "kind": self.kind,
            "reference": self.reference,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class AliasRecord:
    alias_id: str
    raw_alias: str
    target_canonical_key: str
    province_scope: str | None
    type_scope: GeographyType | None
    provenance: tuple[ProvenanceRecord, ...]
    note: str
    registry_version: str


@dataclass(frozen=True, slots=True)
class GeographyIdentity:
    canonical_key: str
    canonical_name: str
    type: GeographyType
    province: str | None
    parent: str | None
    aliases: tuple[AliasRecord, ...]
    provenance: tuple[ProvenanceRecord, ...]
    registry_version: str
    official_code: str | None = None
    country_code: str | None = None
    autonomous_community_key: str | None = None
    province_key: str | None = None
    query_label: str | None = None
    control_digit: str | None = None
    source: str | None = None
    source_version: str | None = None


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    area_canonical_key: str
    member_canonical_key: str
    provenance: tuple[ProvenanceRecord, ...]
    registry_version: str


@dataclass(frozen=True, slots=True)
class ResolutionProvenance:
    match_kind: str
    identity_provenance: tuple[ProvenanceRecord, ...] = ()
    alias_provenance: tuple[ProvenanceRecord, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_kind": self.match_kind,
            "identity_provenance": [item.to_dict() for item in self.identity_provenance],
            "alias_provenance": [item.to_dict() for item in self.alias_provenance],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class GeographyResolution:
    raw_value: str | None
    normalized_input: str
    status: ResolutionStatus
    canonical_key: str | None
    canonical_name: str | None
    canonical_type: GeographyType | None
    matched_alias: str | None
    province_hint: str | None
    province_hint_key: str | None
    expected_type: tuple[GeographyType, ...]
    provenance: ResolutionProvenance
    registry_version: str
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, JSON-compatible representation."""
        return {
            "raw_value": self.raw_value,
            "normalized_input": self.normalized_input,
            "status": self.status.value,
            "canonical_key": self.canonical_key,
            "canonical_name": self.canonical_name,
            "canonical_type": self.canonical_type.value if self.canonical_type else None,
            "matched_alias": self.matched_alias,
            "province_hint": self.province_hint,
            "province_hint_key": self.province_hint_key,
            "expected_type": [item.value for item in self.expected_type],
            "provenance": self.provenance.to_dict(),
            "registry_version": self.registry_version,
            "candidates": list(self.candidates),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
