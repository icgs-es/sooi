from __future__ import annotations

import re
from typing import Iterable

from django.apps import apps

from .geography_registry.loader import (
    load_authority_registry,
)
from .geography_registry.resolver import resolve
from .geography_runtime import geography_snapshot_for_profile


SEARCHPROFILE_SOURCE_FIELDS = frozenset(
    {
        "province",
        "zone",
        "geography_scope",
        "municipalities",
        "geographic_area_id",
    }
)

SEARCHPROFILE_SOURCE_ALIASES = frozenset(
    set(SEARCHPROFILE_SOURCE_FIELDS)
    | {"geographic_area"}
)

SEARCHPROFILE_DERIVED_FIELDS = frozenset(
    {
        "geography_bridge_snapshot",
    }
)


CAPTURE_SOURCE_FIELDS = frozenset(
    {
        "province",
        "municipality",
        "zone_text",
    }
)

CAPTURE_DERIVED_FIELDS = frozenset(
    {
        "geo_canonical_key",
        "geo_resolution_status",
        "geo_resolution_method",
        "geo_registry_version",
        "geo_registry_digest",
    }
)


OPPORTUNITY_SOURCE_FIELDS = frozenset(
    {
        "province",
        "municipality",
        "zone",
        "captured_property_id",
    }
)

OPPORTUNITY_SOURCE_ALIASES = frozenset(
    set(OPPORTUNITY_SOURCE_FIELDS)
    | {"captured_property"}
)

OPPORTUNITY_DERIVED_FIELDS = CAPTURE_DERIVED_FIELDS


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _status_value(value) -> str:
    return getattr(
        value,
        "value",
        str(value),
    )


def _normalize_update_fields(
    update_fields,
):
    if update_fields is None:
        return None

    if isinstance(
        update_fields,
        str,
    ):
        return {update_fields}

    return set(update_fields)


def _expand_update_fields(
    update_fields,
    derived_fields: Iterable[str],
):
    normalized = (
        _normalize_update_fields(
            update_fields
        )
    )

    if normalized is None:
        return None

    normalized.update(
        derived_fields
    )

    return normalized


def _persisted_source_changed(
    instance,
    source_fields: Iterable[str],
    update_fields,
    source_aliases=None,
) -> bool:
    if (
        instance._state.adding
        or instance.pk is None
    ):
        return True

    normalized = (
        _normalize_update_fields(
            update_fields
        )
    )

    aliases = set(
        source_aliases
        or source_fields
    )

    if normalized is not None:
        return bool(
            normalized & aliases
        )

    fields = tuple(
        source_fields
    )

    persisted = (
        type(instance)
        ._default_manager
        .filter(
            pk=instance.pk
        )
        .values(
            *fields
        )
        .first()
    )

    if persisted is None:
        return True

    for field in fields:
        if (
            persisted.get(field)
            != getattr(
                instance,
                field,
            )
        ):
            return True

    return False


def build_search_profile_snapshot(
    profile,
):
    return geography_snapshot_for_profile(
        profile
    )


def prepare_search_profile_for_save(
    profile,
    update_fields=None,
):
    missing = (
        profile.geography_bridge_snapshot
        is None
    )

    changed = (
        _persisted_source_changed(
            profile,
            SEARCHPROFILE_SOURCE_FIELDS,
            update_fields,
            SEARCHPROFILE_SOURCE_ALIASES,
        )
    )

    refresh = (
        profile._state.adding
        or missing
        or changed
    )

    if refresh:
        profile.geography_bridge_snapshot = (
            build_search_profile_snapshot(
                profile
            )
        )

        update_fields = (
            _expand_update_fields(
                update_fields,
                SEARCHPROFILE_DERIVED_FIELDS,
            )
        )

    return (
        update_fields,
        refresh,
    )


def _direct_municipality(
    raw,
    province,
    registry,
):
    result = resolve(
        raw,
        province_hint=(
            province or None
        ),
        expected_type="municipality",
        registry=registry,
    )

    if not result.canonical_key:
        return None

    return {
        "canonical_key": (
            result.canonical_key
        ),
        "status": _status_value(
            result.status
        ),
    }


def build_scalar_bridge(
    raw,
    province,
):
    raw = _clean(raw)
    province = _clean(province)

    registry = (
        load_authority_registry()
    )

    version = (
        registry.registry_version
    )

    digest = (
        registry.content_digest
    )


    if not raw:
        return {
            "geo_canonical_key": None,
            "geo_resolution_status": "EMPTY",
            "geo_resolution_method": "NONE",
            "geo_registry_version": version,
            "geo_registry_digest": digest,
        }


    direct = _direct_municipality(
        raw,
        province,
        registry,
    )

    if direct:
        return {
            "geo_canonical_key": (
                direct[
                    "canonical_key"
                ]
            ),
            "geo_resolution_status": (
                direct[
                    "status"
                ]
            ),
            "geo_resolution_method": (
                "REGISTRY_DIRECT"
            ),
            "geo_registry_version": version,
            "geo_registry_digest": digest,
        }


    match = re.search(
        r"\(([^()]+)\)\s*$",
        raw,
    )

    if match:
        candidate = (
            match.group(1)
            .strip()
        )

        resolved = (
            _direct_municipality(
                candidate,
                province,
                registry,
            )
        )

        if resolved:
            return {
                "geo_canonical_key": (
                    resolved[
                        "canonical_key"
                    ]
                ),
                "geo_resolution_status": (
                    resolved[
                        "status"
                    ]
                ),
                "geo_resolution_method": (
                    "EXPLICIT_PARENTHETICAL_MUNICIPALITY"
                ),
                "geo_registry_version": version,
                "geo_registry_digest": digest,
            }


    if "," in raw:
        candidate = (
            raw.split(
                ",",
                1,
            )[0]
            .strip()
        )

        if candidate:
            resolved = (
                _direct_municipality(
                    candidate,
                    province,
                    registry,
                )
            )

            if resolved:
                return {
                    "geo_canonical_key": (
                        resolved[
                            "canonical_key"
                        ]
                    ),
                    "geo_resolution_status": (
                        resolved[
                            "status"
                        ]
                    ),
                    "geo_resolution_method": (
                        "EXPLICIT_LEADING_MUNICIPALITY"
                    ),
                    "geo_registry_version": version,
                    "geo_registry_digest": digest,
                }


    return {
        "geo_canonical_key": None,
        "geo_resolution_status": "UNRESOLVED",
        "geo_resolution_method": "NONE",
        "geo_registry_version": version,
        "geo_registry_digest": digest,
    }


def _scalar_bridge_complete(
    instance,
) -> bool:
    return all(
        getattr(
            instance,
            field,
        )
        is not None
        for field in (
            "geo_resolution_status",
            "geo_resolution_method",
            "geo_registry_version",
            "geo_registry_digest",
        )
    )


def build_captured_property_bridge(
    captured_property,
):
    return build_scalar_bridge(
        captured_property.municipality,
        captured_property.province,
    )


def prepare_captured_property_for_save(
    captured_property,
    update_fields=None,
):
    missing = not (
        _scalar_bridge_complete(
            captured_property
        )
    )

    changed = (
        _persisted_source_changed(
            captured_property,
            CAPTURE_SOURCE_FIELDS,
            update_fields,
        )
    )

    refresh = (
        captured_property._state.adding
        or missing
        or changed
    )

    if refresh:
        payload = (
            build_captured_property_bridge(
                captured_property
            )
        )

        for field, value in payload.items():
            setattr(
                captured_property,
                field,
                value,
            )

        update_fields = (
            _expand_update_fields(
                update_fields,
                CAPTURE_DERIVED_FIELDS,
            )
        )

    return (
        update_fields,
        refresh,
    )


def _captured_bridge_for_inheritance(
    captured_property,
):
    if captured_property is None:
        return None

    if (
        _scalar_bridge_complete(
            captured_property
        )
    ):
        return {
            field: getattr(
                captured_property,
                field,
            )
            for field in (
                CAPTURE_DERIVED_FIELDS
            )
        }

    return (
        build_captured_property_bridge(
            captured_property
        )
    )


def build_property_opportunity_bridge(
    opportunity,
    *,
    captured_property=None,
):
    direct = build_scalar_bridge(
        opportunity.municipality,
        opportunity.province,
    )

    if (
        direct[
            "geo_resolution_status"
        ]
        != "EMPTY"
    ):
        return direct


    capture_id = (
        opportunity.captured_property_id
    )

    if not capture_id:
        return direct


    capture = captured_property

    if capture is None:
        capture = (
            opportunity._state
            .fields_cache
            .get(
                "captured_property"
            )
        )


    if (
        capture is None
        or capture.pk
        != capture_id
    ):
        CapturedProperty = (
            apps.get_model(
                "inmuebles",
                "CapturedProperty",
            )
        )

        capture = (
            CapturedProperty.objects
            .filter(
                pk=capture_id
            )
            .only(
                "pk",
                "province",
                "municipality",
                "geo_canonical_key",
                "geo_resolution_status",
                "geo_resolution_method",
                "geo_registry_version",
                "geo_registry_digest",
            )
            .first()
        )


    capture_bridge = (
        _captured_bridge_for_inheritance(
            capture
        )
    )


    if not (
        capture_bridge
        and capture_bridge.get(
            "geo_canonical_key"
        )
    ):
        return direct


    return {
        "geo_canonical_key": (
            capture_bridge[
                "geo_canonical_key"
            ]
        ),
        "geo_resolution_status": (
            "INHERITED"
        ),
        "geo_resolution_method": (
            "INHERITED_CAPTURED_PROPERTY"
        ),
        "geo_registry_version": (
            capture_bridge[
                "geo_registry_version"
            ]
        ),
        "geo_registry_digest": (
            capture_bridge[
                "geo_registry_digest"
            ]
        ),
    }


def prepare_property_opportunity_for_save(
    opportunity,
    update_fields=None,
):
    missing = not (
        _scalar_bridge_complete(
            opportunity
        )
    )

    changed = (
        _persisted_source_changed(
            opportunity,
            OPPORTUNITY_SOURCE_FIELDS,
            update_fields,
            OPPORTUNITY_SOURCE_ALIASES,
        )
    )

    refresh = (
        opportunity._state.adding
        or missing
        or changed
    )

    if refresh:
        payload = (
            build_property_opportunity_bridge(
                opportunity
            )
        )

        for field, value in payload.items():
            setattr(
                opportunity,
                field,
                value,
            )

        update_fields = (
            _expand_update_fields(
                update_fields,
                OPPORTUNITY_DERIVED_FIELDS,
            )
        )

    return (
        update_fields,
        refresh,
    )


def refresh_inherited_opportunity_from_capture(
    captured_property,
) -> int:
    if not captured_property.pk:
        return 0

    PropertyOpportunity = (
        apps.get_model(
            "seguimiento",
            "PropertyOpportunity",
        )
    )

    opportunity = (
        PropertyOpportunity.objects
        .filter(
            captured_property_id=(
                captured_property.pk
            )
        )
        .only(
            "pk",
            "province",
            "municipality",
            "zone",
            "captured_property_id",
            "geo_canonical_key",
            "geo_resolution_status",
            "geo_resolution_method",
            "geo_registry_version",
            "geo_registry_digest",
        )
        .first()
    )

    if opportunity is None:
        return 0

    if _clean(
        opportunity.municipality
    ):
        return 0

    payload = (
        build_property_opportunity_bridge(
            opportunity,
            captured_property=(
                captured_property
            ),
        )
    )

    current = {
        field: getattr(
            opportunity,
            field,
        )
        for field in (
            OPPORTUNITY_DERIVED_FIELDS
        )
    }

    if current == payload:
        return 0

    return (
        PropertyOpportunity.objects
        .filter(
            pk=opportunity.pk
        )
        .update(
            **payload
        )
    )


def refresh_search_profiles_by_ids(
    profile_ids,
) -> int:
    profile_ids = list(
        dict.fromkeys(
            profile_ids
        )
    )

    if not profile_ids:
        return 0

    SearchProfile = (
        apps.get_model(
            "busquedas",
            "SearchProfile",
        )
    )

    profiles = list(
        SearchProfile.objects
        .filter(
            pk__in=profile_ids
        )
        .select_related(
            "geographic_area"
        )
        .order_by("pk")
    )

    for profile in profiles:
        profile.geography_bridge_snapshot = (
            build_search_profile_snapshot(
                profile
            )
        )

    if profiles:
        SearchProfile.objects.bulk_update(
            profiles,
            [
                "geography_bridge_snapshot",
            ],
            batch_size=100,
        )

    return len(profiles)


def refresh_search_profiles_for_area(
    geographic_area_id,
) -> int:
    if not geographic_area_id:
        return 0

    SearchProfile = (
        apps.get_model(
            "busquedas",
            "SearchProfile",
        )
    )

    profile_ids = list(
        SearchProfile.objects
        .filter(
            geography_scope="named_area",
            geographic_area_id=(
                geographic_area_id
            ),
        )
        .values_list(
            "pk",
            flat=True,
        )
    )

    return (
        refresh_search_profiles_by_ids(
            profile_ids
        )
    )
