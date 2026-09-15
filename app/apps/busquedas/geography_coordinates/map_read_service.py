from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .loader import (
    MAP_DISPLAY_LABEL_ES,
    CoordinateRegistry,
    load_coordinate_registry,
)


MAP_READ_SERVICE_CONTRACT = (
    "SOOI_G3_MAP_READ_SERVICE_V1"
)

STATUS_READY = "READY"

STATUS_NO_CANONICAL_IDENTITY = (
    "NO_CANONICAL_IDENTITY"
)

STATUS_NO_COORDINATE_RECORD = (
    "NO_COORDINATE_RECORD"
)

STATUS_UNSAFE_COORDINATE = (
    "UNSAFE_COORDINATE"
)


@dataclass(
    frozen=True,
    slots=True,
)
class OpportunityMapPoint:
    opportunity_id: int | None

    canonical_key: str

    latitude: float
    longitude: float

    precision_class: str
    approximation_label_es: str

    coordinate_reference_system: str

    coordinate_source: str
    coordinate_source_version: str
    source_coordinate_origin: str

    registry_version: str
    identity_authority: str

    attribution: str

    @property
    def lon_lat(
        self,
    ) -> tuple[float, float]:
        return (
            self.longitude,
            self.latitude,
        )

    def as_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


@dataclass(
    frozen=True,
    slots=True,
)
class OpportunityMapResolution:
    opportunity_id: int | None

    canonical_key: str | None

    status: str

    point: OpportunityMapPoint | None

    @property
    def ready(
        self,
    ) -> bool:
        return (
            self.status
            == STATUS_READY
            and self.point is not None
        )

    def as_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "opportunity_id":
                self.opportunity_id,

            "canonical_key":
                self.canonical_key,

            "status":
                self.status,

            "point": (
                self.point.as_dict()
                if self.point is not None
                else None
            ),
        }


def _read_value(
    source: Any,
    name: str,
) -> Any:
    if isinstance(
        source,
        Mapping,
    ):
        return source.get(
            name
        )

    return getattr(
        source,
        name,
        None,
    )


def _opportunity_id(
    opportunity: Any,
) -> int | None:
    value = _read_value(
        opportunity,
        "pk",
    )

    if value is None:
        value = _read_value(
            opportunity,
            "id",
        )

    if value is None:
        return None

    return int(value)


def _canonical_key(
    opportunity: Any,
) -> str | None:
    value = _read_value(
        opportunity,
        "geo_canonical_key",
    )

    if value is None:
        return None

    value = str(
        value
    ).strip()

    return (
        value
        if value
        else None
    )


def resolve_opportunity_map_point(
    opportunity: Any,
    *,
    registry: CoordinateRegistry | None = None,
) -> OpportunityMapResolution:
    """
    Resolve one opportunity against the immutable
    municipality coordinate authority.

    Fail-closed rules:

    - no canonical identity -> no point;
    - canonical identity with no coordinate record -> no point;
    - non-map-safe coordinate record -> no point.

    This service never interprets the municipality reference
    point as the physical location of the property.
    """

    opportunity_id = _opportunity_id(
        opportunity
    )

    canonical_key = _canonical_key(
        opportunity
    )

    if canonical_key is None:
        return OpportunityMapResolution(
            opportunity_id=opportunity_id,
            canonical_key=None,
            status=STATUS_NO_CANONICAL_IDENTITY,
            point=None,
        )

    if registry is None:
        registry = (
            load_coordinate_registry()
        )

    coordinate = registry.get(
        canonical_key
    )

    if coordinate is None:
        return OpportunityMapResolution(
            opportunity_id=opportunity_id,
            canonical_key=canonical_key,
            status=STATUS_NO_COORDINATE_RECORD,
            point=None,
        )

    if not coordinate.map_safe:
        return OpportunityMapResolution(
            opportunity_id=opportunity_id,
            canonical_key=canonical_key,
            status=STATUS_UNSAFE_COORDINATE,
            point=None,
        )

    source = (
        registry.manifest.get(
            "source"
        )
        or {}
    )

    attribution = str(
        source.get(
            "attribution",
            "",
        )
    ).strip()

    if not attribution:
        # Missing source attribution is a broken
        # coordinate authority contract. Fail closed.
        return OpportunityMapResolution(
            opportunity_id=opportunity_id,
            canonical_key=canonical_key,
            status=STATUS_UNSAFE_COORDINATE,
            point=None,
        )

    registry_version = str(
        registry.manifest.get(
            "registry_version",
            "",
        )
    )

    identity_authority = str(
        registry.manifest.get(
            "identity_authority",
            "",
        )
    )

    point = OpportunityMapPoint(
        opportunity_id=opportunity_id,

        canonical_key=canonical_key,

        latitude=coordinate.latitude,
        longitude=coordinate.longitude,

        precision_class=(
            coordinate.precision_class
        ),

        approximation_label_es=(
            MAP_DISPLAY_LABEL_ES
        ),

        coordinate_reference_system=(
            coordinate
            .coordinate_reference_system
        ),

        coordinate_source=(
            coordinate.source_name
        ),

        coordinate_source_version=(
            coordinate.source_version
        ),

        source_coordinate_origin=(
            coordinate
            .source_coordinate_origin
        ),

        registry_version=registry_version,

        identity_authority=(
            identity_authority
        ),

        attribution=attribution,
    )

    return OpportunityMapResolution(
        opportunity_id=opportunity_id,
        canonical_key=canonical_key,
        status=STATUS_READY,
        point=point,
    )


def resolve_opportunity_map_points(
    opportunities: Iterable[Any],
    *,
    registry: CoordinateRegistry | None = None,
) -> tuple[
    OpportunityMapResolution,
    ...,
]:
    if registry is None:
        registry = (
            load_coordinate_registry()
        )

    return tuple(
        resolve_opportunity_map_point(
            opportunity,
            registry=registry,
        )
        for opportunity in opportunities
    )


def map_points_for_opportunities(
    opportunities: Iterable[Any],
    *,
    registry: CoordinateRegistry | None = None,
) -> tuple[
    OpportunityMapPoint,
    ...,
]:
    resolutions = (
        resolve_opportunity_map_points(
            opportunities,
            registry=registry,
        )
    )

    return tuple(
        resolution.point
        for resolution in resolutions
        if (
            resolution.ready
            and resolution.point
            is not None
        )
    )


def map_read_summary(
    opportunities: Iterable[Any],
    *,
    registry: CoordinateRegistry | None = None,
) -> dict[str, int]:
    resolutions = (
        resolve_opportunity_map_points(
            opportunities,
            registry=registry,
        )
    )

    counts = {
        STATUS_READY: 0,
        STATUS_NO_CANONICAL_IDENTITY: 0,
        STATUS_NO_COORDINATE_RECORD: 0,
        STATUS_UNSAFE_COORDINATE: 0,
    }

    for resolution in resolutions:
        counts[
            resolution.status
        ] += 1

    counts["TOTAL"] = len(
        resolutions
    )

    return counts
