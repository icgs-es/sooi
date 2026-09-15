from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .models import CoordinatePoint


REGISTRY_VERSION = "v1"

REGISTRY_NAME = (
    "SOOI_GEOGRAPHY_COORDINATE_REGISTRY_ES_V1"
)

IDENTITY_AUTHORITY = (
    "SOOI_GEOGRAPHY_REGISTRY_ES_V3"
)

POINTS_CONTRACT = (
    "SOOI_G3_COORDINATE_REGISTRY_V1"
)

MANIFEST_CONTRACT = (
    "SOOI_G3_COORDINATE_REGISTRY_MANIFEST_V1"
)

PRECISION_CLASS = (
    "MUNICIPALITY_POPULATION_CENTROID"
)

MAP_DISPLAY_LABEL_ES = (
    "Ubicación aproximada a nivel municipal"
)

_ALLOWED_QUALITY = {
    "OFFICIAL_SOURCE_METHOD",
    "APPROXIMATE_SOURCE",
    "UNKNOWN_SOURCE",
}

_KEY_RE = re.compile(
    r"^municipality:(\d{5})$"
)


class CoordinateRegistryError(RuntimeError):
    pass


class CoordinateRegistry:
    def __init__(
        self,
        *,
        manifest: dict,
        records: dict[str, CoordinatePoint],
    ):
        self.manifest = manifest
        self._records = records

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, canonical_key: str) -> bool:
        return canonical_key in self._records

    def get(
        self,
        canonical_key: str | None,
    ) -> CoordinatePoint | None:
        if not canonical_key:
            return None

        return self._records.get(
            canonical_key
        )

    def require(
        self,
        canonical_key: str,
    ) -> CoordinatePoint:
        point = self.get(
            canonical_key
        )

        if point is None:
            raise KeyError(
                canonical_key
            )

        return point

    def get_map_safe(
        self,
        canonical_key: str | None,
    ) -> CoordinatePoint | None:
        point = self.get(
            canonical_key
        )

        if point is None:
            return None

        if not point.map_safe:
            return None

        return point

    def values(
        self,
    ) -> tuple[CoordinatePoint, ...]:
        return tuple(
            self._records.values()
        )


def _data_dir(
    version: str,
) -> Path:
    if version != REGISTRY_VERSION:
        raise CoordinateRegistryError(
            f"Unsupported coordinate registry "
            f"version: {version}"
        )

    return (
        Path(__file__).resolve().parent
        / "data"
        / version
    )


def _sha256_bytes(
    payload: bytes,
) -> str:
    return (
        "sha256:"
        + sha256(payload).hexdigest()
    )


def _load_json(
    path: Path,
) -> tuple[dict, bytes]:
    payload = path.read_bytes()

    try:
        parsed = json.loads(
            payload.decode("utf-8")
        )
    except Exception as exc:
        raise CoordinateRegistryError(
            f"Invalid JSON: {path}"
        ) from exc

    if not isinstance(
        parsed,
        dict,
    ):
        raise CoordinateRegistryError(
            f"Expected object: {path}"
        )

    return parsed, payload


def _build_point(
    row: dict,
) -> CoordinatePoint:
    canonical_key = str(
        row["canonical_key"]
    )

    match = _KEY_RE.fullmatch(
        canonical_key
    )

    if match is None:
        raise CoordinateRegistryError(
            f"Invalid canonical key: "
            f"{canonical_key}"
        )

    official_code = str(
        row["official_code"]
    ).zfill(5)

    if official_code != match.group(1):
        raise CoordinateRegistryError(
            f"Canonical/code mismatch: "
            f"{canonical_key}"
        )

    latitude = float(
        row["latitude"]
    )

    longitude = float(
        row["longitude"]
    )

    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
    ):
        raise CoordinateRegistryError(
            f"Non-finite coordinates: "
            f"{canonical_key}"
        )

    if not (
        -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    ):
        raise CoordinateRegistryError(
            f"Out-of-range coordinates: "
            f"{canonical_key}"
        )

    if (
        latitude == 0.0
        and longitude == 0.0
    ):
        raise CoordinateRegistryError(
            f"Zero/zero coordinate: "
            f"{canonical_key}"
        )

    precision_class = str(
        row["precision_class"]
    )

    if (
        precision_class
        != PRECISION_CLASS
    ):
        raise CoordinateRegistryError(
            f"Unexpected precision class: "
            f"{canonical_key}"
        )

    quality = str(
        row["source_quality_class"]
    )

    if quality not in _ALLOWED_QUALITY:
        raise CoordinateRegistryError(
            f"Unexpected source quality: "
            f"{canonical_key}"
        )

    return CoordinatePoint(
        canonical_key=canonical_key,
        official_code=official_code,
        latitude=latitude,
        longitude=longitude,
        precision_class=precision_class,
        source_quality_class=quality,
        source_coordinate_origin=str(
            row[
                "source_coordinate_origin"
            ]
        ),
        source_name=str(
            row["source_name"]
        ),
        source_version=str(
            row["source_version"]
        ),
        source_reference=str(
            row["source_reference"]
        ),
        coordinate_reference_system=str(
            row[
                "coordinate_reference_system"
            ]
        ),
        source_record_identity=str(
            row[
                "source_record_identity"
            ]
        ),
        content_digest=str(
            row["content_digest"]
        ),
    )


@lru_cache(maxsize=2)
def load_coordinate_registry(
    version: str = REGISTRY_VERSION,
) -> CoordinateRegistry:
    data_dir = _data_dir(
        version
    )

    manifest, _ = _load_json(
        data_dir / "manifest.json"
    )

    points, points_bytes = _load_json(
        data_dir
        / "municipality_points.json"
    )

    if (
        manifest.get("contract")
        != MANIFEST_CONTRACT
    ):
        raise CoordinateRegistryError(
            "Manifest contract mismatch"
        )

    if (
        manifest.get("registry_version")
        != REGISTRY_NAME
    ):
        raise CoordinateRegistryError(
            "Registry version mismatch"
        )

    if (
        manifest.get("identity_authority")
        != IDENTITY_AUTHORITY
    ):
        raise CoordinateRegistryError(
            "Identity authority mismatch"
        )

    if (
        manifest.get("precision_class")
        != PRECISION_CLASS
    ):
        raise CoordinateRegistryError(
            "Manifest precision mismatch"
        )

    actual_points_digest = (
        _sha256_bytes(
            points_bytes
        )
    )

    if (
        manifest.get("points_sha256")
        != actual_points_digest
    ):
        raise CoordinateRegistryError(
            "Coordinate payload digest mismatch"
        )

    if (
        points.get("contract")
        != POINTS_CONTRACT
    ):
        raise CoordinateRegistryError(
            "Points contract mismatch"
        )

    if (
        points.get("identity_authority")
        != IDENTITY_AUTHORITY
    ):
        raise CoordinateRegistryError(
            "Points identity authority mismatch"
        )

    if (
        points.get("precision_class")
        != PRECISION_CLASS
    ):
        raise CoordinateRegistryError(
            "Points precision mismatch"
        )

    rows = points.get(
        "records"
    )

    if not isinstance(
        rows,
        list,
    ):
        raise CoordinateRegistryError(
            "Coordinate records must be a list"
        )

    if (
        len(rows)
        != manifest.get("record_count")
    ):
        raise CoordinateRegistryError(
            "Record count mismatch"
        )

    if len(rows) != 8132:
        raise CoordinateRegistryError(
            "National municipality coverage "
            "must be 8132"
        )

    records: dict[
        str,
        CoordinatePoint,
    ] = {}

    for row in rows:
        if not isinstance(
            row,
            dict,
        ):
            raise CoordinateRegistryError(
                "Invalid coordinate record"
            )

        point = _build_point(
            row
        )

        if (
            point.canonical_key
            in records
        ):
            raise CoordinateRegistryError(
                "Duplicate canonical key: "
                + point.canonical_key
            )

        records[
            point.canonical_key
        ] = point

    return CoordinateRegistry(
        manifest=manifest,
        records=records,
    )


def coordinate_for_canonical_key(
    canonical_key: str | None,
    *,
    version: str = REGISTRY_VERSION,
) -> CoordinatePoint | None:
    return (
        load_coordinate_registry(
            version
        )
        .get_map_safe(
            canonical_key
        )
    )
