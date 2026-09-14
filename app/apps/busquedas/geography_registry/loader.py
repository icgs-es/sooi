"""Fail-fast loader for immutable, declarative geography registries."""

from __future__ import annotations
import os

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Mapping

from .models import (
    AliasRecord,
    GeographyIdentity,
    GeographyType,
    MembershipRecord,
    ProvenanceRecord,
)


class RegistryValidationError(ValueError):
    pass


_ALLOWED_PARENT_TYPES = {
    GeographyType.COUNTRY: frozenset(),
    GeographyType.AUTONOMOUS_COMMUNITY: frozenset({GeographyType.COUNTRY}),
    GeographyType.AUTONOMOUS_CITY: frozenset({GeographyType.COUNTRY}),
    GeographyType.PROVINCE: frozenset({GeographyType.AUTONOMOUS_COMMUNITY}),
    GeographyType.MUNICIPALITY: frozenset({GeographyType.PROVINCE, GeographyType.AUTONOMOUS_CITY}),
    GeographyType.DISTRICT: frozenset({GeographyType.MUNICIPALITY}),
    GeographyType.NEIGHBORHOOD: frozenset({GeographyType.DISTRICT, GeographyType.MUNICIPALITY}),
    GeographyType.ISLAND: frozenset(),
    GeographyType.TERRITORIAL_AREA: frozenset(),
    GeographyType.COMARCA: frozenset({GeographyType.PROVINCE}),
    GeographyType.NAMED_AREA: frozenset({GeographyType.PROVINCE}),
}


def canonical_content_digest(
    identities_document: Any, aliases_document: Any, memberships_document: Any = (),
) -> str:
    payload = {
        "aliases": aliases_document,
        "identities": identities_document,
        "memberships": memberships_document,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _required_text(data: Mapping[str, Any], field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegistryValidationError(f"{context}.{field} must be non-empty text")
    return value.strip()


def _provenance(rows: Any, context: str) -> tuple[ProvenanceRecord, ...]:
    if not isinstance(rows, list) or not rows:
        raise RegistryValidationError(f"{context}.provenance must be a non-empty list")
    records = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RegistryValidationError(f"{context}.provenance[{index}] must be an object")
        item_context = f"{context}.provenance[{index}]"
        records.append(ProvenanceRecord(
            provenance_id=_required_text(row, "provenance_id", item_context),
            kind=_required_text(row, "kind", item_context),
            reference=_required_text(row, "reference", item_context),
            note=_required_text(row, "note", item_context),
        ))
    return tuple(records)


@dataclass(frozen=True, slots=True)
class GeographyRegistry:
    registry_version: str
    schema_version: int
    content_digest: str
    identities: Mapping[str, GeographyIdentity]
    aliases: tuple[AliasRecord, ...]
    memberships: tuple[MembershipRecord, ...]
    members_by_area: Mapping[str, tuple[str, ...]]
    name_index: Mapping[str, tuple[str, ...]]
    alias_index: Mapping[str, tuple[AliasRecord, ...]]
    official_index: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_documents(
        cls,
        manifest: Mapping[str, Any],
        identities_document: Any,
        aliases_document: Any,
        memberships_document: Any = (),
    ) -> "GeographyRegistry":
        if not isinstance(manifest, dict):
            raise RegistryValidationError("manifest must be an object")
        registry_version = _required_text(manifest, "registry_version", "manifest")
        schema_version = manifest.get("schema_version")
        if schema_version not in {1, 2}:
            raise RegistryValidationError("manifest.schema_version must be 1 or 2")
        expected_digest = _required_text(manifest, "content_digest", "manifest")
        actual_digest = canonical_content_digest(
            identities_document, aliases_document, memberships_document
        )
        if expected_digest != actual_digest:
            raise RegistryValidationError(
                f"registry digest mismatch: expected {expected_digest}, got {actual_digest}"
            )
        if not isinstance(identities_document, list):
            raise RegistryValidationError("identities document must be a list")
        if not isinstance(aliases_document, list):
            raise RegistryValidationError("aliases document must be a list")
        if not isinstance(memberships_document, (list, tuple)):
            raise RegistryValidationError("memberships document must be a list")

        raw_identities: dict[str, tuple[dict[str, Any], GeographyIdentity]] = {}
        for index, row in enumerate(identities_document):
            context = f"identities[{index}]"
            if not isinstance(row, dict):
                raise RegistryValidationError(f"{context} must be an object")
            key = _required_text(row, "canonical_key", context)
            if key in raw_identities:
                raise RegistryValidationError(f"duplicate canonical_key: {key}")
            try:
                geo_type = GeographyType(_required_text(row, "type", context))
            except ValueError as exc:
                raise RegistryValidationError(f"{context}.type is invalid") from exc
            province = row.get("province")
            parent = row.get("parent")
            for field, value in (("province", province), ("parent", parent)):
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise RegistryValidationError(f"{context}.{field} must be text or null")
            identity = GeographyIdentity(
                canonical_key=key,
                canonical_name=_required_text(row, "canonical_name", context),
                type=geo_type,
                province=province,
                parent=parent,
                aliases=(),
                provenance=_provenance(row.get("provenance"), context),
                registry_version=registry_version,
                official_code=(str(row["official_code"]) if row.get("official_code") is not None else None),
                country_code=row.get("country_code"),
                autonomous_community_key=row.get("autonomous_community_key"),
                province_key=row.get("province_key"),
                query_label=row.get("query_label"),
                control_digit=(str(row["control_digit"]) if row.get("control_digit") is not None else None),
                source=row.get("source"),
                source_version=row.get("source_version"),
            )
            raw_identities[key] = (row, identity)

        identities = {key: item for key, (_, item) in raw_identities.items()}
        cls._validate_references_and_hierarchy(identities, schema_version=schema_version)

        aliases = cls._load_aliases(aliases_document, identities, registry_version)
        memberships = cls._load_memberships(
            list(memberships_document), identities, registry_version
        )
        aliases_by_target: dict[str, list[AliasRecord]] = {key: [] for key in identities}
        for alias in aliases:
            aliases_by_target[alias.target_canonical_key].append(alias)
        identities = {
            key: GeographyIdentity(
                canonical_key=item.canonical_key,
                canonical_name=item.canonical_name,
                type=item.type,
                province=item.province,
                parent=item.parent,
                aliases=tuple(aliases_by_target[key]),
                provenance=item.provenance,
                registry_version=item.registry_version,
                official_code=item.official_code,
                country_code=item.country_code,
                autonomous_community_key=item.autonomous_community_key,
                province_key=item.province_key,
                query_label=item.query_label,
                control_digit=item.control_digit,
                source=item.source,
                source_version=item.source_version,
            )
            for key, item in identities.items()
        }

        from .resolver import normalize_geography_input
        name_index_mut: dict[str, list[str]] = {}
        alias_index_mut: dict[str, list[AliasRecord]] = {}
        for key, identity in identities.items():
            name_index_mut.setdefault(normalize_geography_input(identity.canonical_name), []).append(key)
        for alias in aliases:
            alias_index_mut.setdefault(normalize_geography_input(alias.raw_alias), []).append(alias)
        cls._validate_lookup_collisions(identities, name_index_mut, alias_index_mut)

        name_index = MappingProxyType({
            lookup: tuple(sorted(keys)) for lookup, keys in name_index_mut.items()
        })
        alias_index = MappingProxyType({
            lookup: tuple(sorted(rows, key=lambda row: row.alias_id))
            for lookup, rows in alias_index_mut.items()
        })
        official_mut: dict[str, list[str]] = {}
        for key, identity in identities.items():
            if identity.official_code:
                official_mut.setdefault(identity.official_code, []).append(key)
        official_index = MappingProxyType({
            code: tuple(sorted(keys)) for code, keys in official_mut.items()
        })
        members_mut: dict[str, list[str]] = {}
        for membership in memberships:
            members_mut.setdefault(membership.area_canonical_key, []).append(
                membership.member_canonical_key
            )
        members_by_area = MappingProxyType({
            key: tuple(sorted(values)) for key, values in members_mut.items()
        })
        return cls(
            registry_version=registry_version,
            schema_version=schema_version,
            content_digest=actual_digest,
            identities=MappingProxyType(identities),
            aliases=tuple(aliases),
            memberships=memberships,
            members_by_area=members_by_area,
            name_index=name_index,
            alias_index=alias_index,
            official_index=official_index,
        )

    @staticmethod
    def _load_memberships(
        document: list[Any],
        identities: Mapping[str, GeographyIdentity],
        registry_version: str,
    ) -> tuple[MembershipRecord, ...]:
        memberships, pairs = [], set()
        area_types = {GeographyType.COMARCA, GeographyType.NAMED_AREA}
        member_types = {
            GeographyType.MUNICIPALITY,
            GeographyType.DISTRICT,
            GeographyType.NEIGHBORHOOD,
        }
        for index, row in enumerate(document):
            context = f"memberships[{index}]"
            if not isinstance(row, dict):
                raise RegistryValidationError(f"{context} must be an object")
            area_key = _required_text(row, "area_canonical_key", context)
            member_key = _required_text(row, "member_canonical_key", context)
            if area_key not in identities:
                raise RegistryValidationError(f"{context} references missing area {area_key}")
            if member_key not in identities:
                raise RegistryValidationError(f"{context} references missing member {member_key}")
            area, member = identities[area_key], identities[member_key]
            if area.type not in area_types:
                raise RegistryValidationError(f"{context} area is not comarca or named_area")
            if member.type not in member_types:
                raise RegistryValidationError(f"{context} member has invalid type")
            if area.province != member.province:
                raise RegistryValidationError(f"{context} crosses province boundaries")
            pair = (area_key, member_key)
            if pair in pairs:
                raise RegistryValidationError(f"duplicate membership: {area_key} -> {member_key}")
            pairs.add(pair)
            memberships.append(MembershipRecord(
                area_canonical_key=area_key,
                member_canonical_key=member_key,
                provenance=_provenance(row.get("provenance"), context),
                registry_version=registry_version,
            ))
        return tuple(memberships)

    @staticmethod
    def _load_aliases(
        document: list[Any],
        identities: Mapping[str, GeographyIdentity],
        registry_version: str,
    ) -> tuple[AliasRecord, ...]:
        aliases, alias_ids = [], set()
        for index, row in enumerate(document):
            context = f"aliases[{index}]"
            if not isinstance(row, dict):
                raise RegistryValidationError(f"{context} must be an object")
            alias_id = _required_text(row, "alias_id", context)
            if alias_id in alias_ids:
                raise RegistryValidationError(f"duplicate alias_id: {alias_id}")
            alias_ids.add(alias_id)
            target = _required_text(row, "target_canonical_key", context)
            if target not in identities:
                raise RegistryValidationError(f"{context} references missing target {target}")
            province_scope = row.get("province_scope")
            if province_scope is not None:
                if province_scope not in identities:
                    raise RegistryValidationError(
                        f"{context} references missing province_scope {province_scope}"
                    )
                if identities[province_scope].type is not GeographyType.PROVINCE:
                    raise RegistryValidationError(f"{context}.province_scope is not a province")
            type_scope_raw = row.get("type_scope")
            try:
                type_scope = GeographyType(type_scope_raw) if type_scope_raw is not None else None
            except ValueError as exc:
                raise RegistryValidationError(f"{context}.type_scope is invalid") from exc
            if type_scope is not None and type_scope is not identities[target].type:
                raise RegistryValidationError(f"{context}.type_scope contradicts target type")
            if (
                province_scope is not None
                and identities[target].province != province_scope
                and identities[target].canonical_key != province_scope
            ):
                raise RegistryValidationError(f"{context}.province_scope contradicts target province")
            aliases.append(AliasRecord(
                alias_id=alias_id,
                raw_alias=_required_text(row, "raw_alias", context),
                target_canonical_key=target,
                province_scope=province_scope,
                type_scope=type_scope,
                provenance=_provenance(row.get("provenance"), context),
                note=_required_text(row, "note", context),
                registry_version=registry_version,
            ))
        return tuple(aliases)

    @staticmethod
    def _validate_references_and_hierarchy(
        identities: Mapping[str, GeographyIdentity], *, schema_version: int = 1,
    ) -> None:
        # Validate references first so cycle diagnostics are deterministic even
        # when a malicious cycle also violates the parent-type contract.
        for identity in identities.values():
            if identity.parent is not None and identity.parent not in identities:
                raise RegistryValidationError(
                    f"identity {identity.canonical_key} references missing parent {identity.parent}"
                )
            if identity.province is not None:
                province = identities.get(identity.province)
                if province is None:
                    raise RegistryValidationError(
                        f"identity {identity.canonical_key} references missing province {identity.province}"
                    )
                if province.type is not GeographyType.PROVINCE:
                    raise RegistryValidationError(
                        f"identity {identity.canonical_key} province reference is not a province"
                    )

        visiting, visited = set(), set()

        def visit(key: str) -> None:
            if key in visiting:
                raise RegistryValidationError(f"parent cycle detected at {key}")
            if key in visited:
                return
            visiting.add(key)
            parent = identities[key].parent
            if parent is not None:
                visit(parent)
            visiting.remove(key)
            visited.add(key)

        for key in identities:
            visit(key)

        for identity in identities.values():
            allowed = _ALLOWED_PARENT_TYPES[identity.type]
            if not allowed:
                if identity.parent is not None:
                    raise RegistryValidationError(
                        f"identity {identity.canonical_key} type {identity.type.value} cannot have a parent"
                    )
            elif identity.parent is None:
                if not (schema_version == 1 and identity.type is GeographyType.PROVINCE):
                    raise RegistryValidationError(
                        f"identity {identity.canonical_key} type {identity.type.value} requires a parent"
                    )
                continue
            elif identities[identity.parent].type not in allowed:
                raise RegistryValidationError(
                    f"identity {identity.canonical_key} has invalid parent type"
                )
            elif identity.type is not GeographyType.PROVINCE:
                parent = identities[identity.parent]
                parent_province = (
                    parent.canonical_key
                    if parent.type is GeographyType.PROVINCE
                    else parent.province
                )
                if parent_province != identity.province:
                    raise RegistryValidationError(
                        f"identity {identity.canonical_key} parent belongs to another province"
                    )
            if identity.type is GeographyType.PROVINCE and identity.province is not None:
                raise RegistryValidationError(
                    f"province identity {identity.canonical_key} cannot reference a province"
                )
            if identity.type in {GeographyType.MUNICIPALITY, GeographyType.DISTRICT, GeographyType.NEIGHBORHOOD} and identity.province is None and not (
                schema_version == 2 and identity.type is GeographyType.MUNICIPALITY and identity.parent and identities[identity.parent].type is GeographyType.AUTONOMOUS_CITY
            ):
                raise RegistryValidationError(
                    f"identity {identity.canonical_key} requires a province reference"
                )

    @staticmethod
    def _validate_lookup_collisions(
        identities: Mapping[str, GeographyIdentity],
        name_index: Mapping[str, list[str]],
        alias_index: Mapping[str, list[AliasRecord]],
    ) -> None:
        lookups = set(name_index) | set(alias_index)
        for lookup in lookups:
            targets = set(name_index.get(lookup, []))
            targets.update(alias.target_canonical_key for alias in alias_index.get(lookup, []))
            by_domain: dict[tuple[str | None, GeographyType], set[str]] = {}
            for target in targets:
                identity = identities[target]
                domain = (identity.province, identity.type)
                by_domain.setdefault(domain, set()).add(target)
            conflicts = [keys for keys in by_domain.values() if len(keys) > 1]
            if conflicts:
                rendered = ", ".join(sorted({key for group in conflicts for key in group}))
                raise RegistryValidationError(
                    f"unresolvable lookup collision for {lookup!r}: {rendered}"
                )


# SOOI product geography authority.
#
# V1 remains the historical fixture/compatibility registry.
# Product consumers use load_authority_registry().
LEGACY_REGISTRY_VERSION = "v1"
PREVIOUS_NATIONAL_REGISTRY_VERSION = "v2"
NATIONAL_REGISTRY_VERSION = "v3"
SUPPORTED_AUTHORITY_REGISTRY_VERSIONS = frozenset({
    LEGACY_REGISTRY_VERSION,
    PREVIOUS_NATIONAL_REGISTRY_VERSION,
    NATIONAL_REGISTRY_VERSION,
})
REGISTRY_VERSION_ENV = "SOOI_GEOGRAPHY_REGISTRY_VERSION"


def authority_registry_version() -> str:
    requested = os.environ.get(
        REGISTRY_VERSION_ENV,
        NATIONAL_REGISTRY_VERSION,
    ).strip().casefold()

    if not requested:
        requested = NATIONAL_REGISTRY_VERSION

    if requested not in SUPPORTED_AUTHORITY_REGISTRY_VERSIONS:
        raise RegistryValidationError(
            "unsupported SOOI geography registry "
            f"authority: {requested!r}"
        )

    return requested


def load_authority_registry() -> GeographyRegistry:
    """Load the single product geography authority."""
    return load_registry(
        authority_registry_version()
    )


def load_default_registry() -> GeographyRegistry:
    return load_registry("v1")


def load_registry(version: str = "v1") -> GeographyRegistry:
    data_root = files(__package__).joinpath("data", version)
    manifest = json.loads(data_root.joinpath("manifest.json").read_text(encoding="utf-8"))
    identities = json.loads(data_root.joinpath("identities.json").read_text(encoding="utf-8"))
    aliases = json.loads(data_root.joinpath("aliases.json").read_text(encoding="utf-8"))
    memberships = json.loads(data_root.joinpath("memberships.json").read_text(encoding="utf-8"))
    return GeographyRegistry.from_documents(manifest, identities, aliases, memberships)


def load_v2_registry() -> GeographyRegistry:
    return load_registry("v2")
