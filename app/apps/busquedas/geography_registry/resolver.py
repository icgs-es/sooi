"""Pure deterministic geography resolver: declared equality, never inference."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from .models import (
    AliasRecord,
    GeographyIdentity,
    GeographyResolution,
    GeographyType,
    ResolutionProvenance,
    ResolutionStatus,
)


def normalize_geography_input(value: object) -> str:
    """Normalize representation only; this creates no semantic equivalence."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _expected_types(value: object) -> tuple[GeographyType, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, GeographyType)):
        values = (value,)
    else:
        try:
            values = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("expected_type must be a geography type or iterable") from exc
    try:
        return tuple(sorted({GeographyType(item) for item in values}, key=lambda item: item.value))
    except ValueError as exc:
        raise ValueError("expected_type contains an invalid geography type") from exc


def _empty_resolution(
    *, raw_name: object, normalized: str, status: ResolutionStatus,
    province_hint: object, province_hint_key: str | None,
    expected: tuple[GeographyType, ...], registry_version: str,
    candidates: tuple[str, ...] = (), notes: tuple[str, ...] = (),
) -> GeographyResolution:
    return GeographyResolution(
        raw_value=None if raw_name is None else str(raw_name),
        normalized_input=normalized,
        status=status,
        canonical_key=None,
        canonical_name=None,
        canonical_type=None,
        matched_alias=None,
        province_hint=None if province_hint is None else str(province_hint),
        province_hint_key=province_hint_key,
        expected_type=expected,
        provenance=ResolutionProvenance(match_kind="none", notes=notes),
        registry_version=registry_version,
        candidates=candidates,
    )


def _matches(registry, normalized: str) -> dict[str, AliasRecord | None]:
    if normalized in registry.identities:
        return {normalized: None}
    matches: dict[str, AliasRecord | None] = {
        key: None for key in registry.name_index.get(normalized, ())
    }
    for key in registry.official_index.get(normalized, ()):
        matches.setdefault(key, None)
    for alias in registry.alias_index.get(normalized, ()):
        matches.setdefault(alias.target_canonical_key, alias)
    return matches


def _resolve_province_hint(registry, province_hint: object) -> str | None:
    normalized = normalize_geography_input(province_hint)
    if not normalized:
        return None
    matches = _matches(registry, normalized)
    keys = [
        key for key in matches
        if registry.identities[key].type is GeographyType.PROVINCE
    ]
    return keys[0] if len(keys) == 1 else None


def resolve(
    raw_name: object,
    province_hint: object = None,
    expected_type: object = None,
    *,
    registry=None,
) -> GeographyResolution:
    """Resolve only canonical names and explicit aliases in an immutable registry."""
    if registry is None:
        registry = _default_registry()
    normalized = normalize_geography_input(raw_name)
    expected = _expected_types(expected_type)
    province_hint_key = _resolve_province_hint(registry, province_hint)
    if not normalized:
        return _empty_resolution(
            raw_name=raw_name, normalized=normalized, status=ResolutionStatus.UNRESOLVED,
            province_hint=province_hint, province_hint_key=province_hint_key,
            expected=expected, registry_version=registry.registry_version,
            notes=("empty_input",),
        )

    matches = _matches(registry, normalized)
    if not matches:
        return _empty_resolution(
            raw_name=raw_name, normalized=normalized, status=ResolutionStatus.UNRESOLVED,
            province_hint=province_hint, province_hint_key=province_hint_key,
            expected=expected, registry_version=registry.registry_version,
            notes=("no_declared_match",),
        )

    original_keys = tuple(sorted(matches))
    remaining = list(original_keys)
    if province_hint_key is not None:
        in_province = [
            key for key in remaining
            if (
                registry.identities[key].type is GeographyType.PROVINCE
                and key == province_hint_key
            ) or registry.identities[key].province == province_hint_key
        ]
        if not in_province:
            if len(original_keys) == 1:
                return _resolved_diagnostic(
                    registry, raw_name, normalized, matches, original_keys[0],
                    ResolutionStatus.OUTSIDE_PROVINCE, province_hint,
                    province_hint_key, expected, ("province_hint_mismatch",),
                )
            return _empty_resolution(
                raw_name=raw_name, normalized=normalized, status=ResolutionStatus.AMBIGUOUS,
                province_hint=province_hint, province_hint_key=province_hint_key,
                expected=expected, registry_version=registry.registry_version,
                candidates=original_keys, notes=("multiple_matches_outside_province_hint",),
            )
        remaining = in_province

    if expected:
        correct_type = [key for key in remaining if registry.identities[key].type in expected]
        if not correct_type:
            if len(remaining) == 1:
                return _resolved_diagnostic(
                    registry, raw_name, normalized, matches, remaining[0],
                    ResolutionStatus.INVALID_TYPE, province_hint,
                    province_hint_key, expected, ("expected_type_mismatch",),
                )
            return _empty_resolution(
                raw_name=raw_name, normalized=normalized, status=ResolutionStatus.AMBIGUOUS,
                province_hint=province_hint, province_hint_key=province_hint_key,
                expected=expected, registry_version=registry.registry_version,
                candidates=tuple(sorted(remaining)), notes=("multiple_matches_wrong_type",),
            )
        remaining = correct_type

    if len(remaining) != 1:
        return _empty_resolution(
            raw_name=raw_name, normalized=normalized, status=ResolutionStatus.AMBIGUOUS,
            province_hint=province_hint, province_hint_key=province_hint_key,
            expected=expected, registry_version=registry.registry_version,
            candidates=tuple(sorted(remaining)), notes=("multiple_declared_matches",),
        )

    key = remaining[0]
    alias = matches[key]
    identity = registry.identities[key]
    return GeographyResolution(
        raw_value=None if raw_name is None else str(raw_name),
        normalized_input=normalized,
        status=ResolutionStatus.ALIAS_RESOLVED if alias else ResolutionStatus.EXACT,
        canonical_key=identity.canonical_key,
        canonical_name=identity.canonical_name,
        canonical_type=identity.type,
        matched_alias=alias.raw_alias if alias else None,
        province_hint=None if province_hint is None else str(province_hint),
        province_hint_key=province_hint_key,
        expected_type=expected,
        provenance=_resolution_provenance(identity, alias),
        registry_version=registry.registry_version,
    )


def _resolution_provenance(
    identity: GeographyIdentity, alias: AliasRecord | None, notes: tuple[str, ...] = (),
) -> ResolutionProvenance:
    return ResolutionProvenance(
        match_kind="alias" if alias else "canonical_name",
        identity_provenance=identity.provenance,
        alias_provenance=alias.provenance if alias else (),
        notes=notes,
    )


def _resolved_diagnostic(
    registry, raw_name, normalized, matches, key, status, province_hint,
    province_hint_key, expected, notes,
) -> GeographyResolution:
    identity = registry.identities[key]
    alias = matches[key]
    return GeographyResolution(
        raw_value=None if raw_name is None else str(raw_name),
        normalized_input=normalized,
        status=status,
        canonical_key=identity.canonical_key,
        canonical_name=identity.canonical_name,
        canonical_type=identity.type,
        matched_alias=alias.raw_alias if alias else None,
        province_hint=None if province_hint is None else str(province_hint),
        province_hint_key=province_hint_key,
        expected_type=expected,
        provenance=_resolution_provenance(identity, alias, notes),
        registry_version=registry.registry_version,
    )


@lru_cache(maxsize=1)
def _default_registry():
    from .loader import load_default_registry
    return load_default_registry()
