from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CoordinatePoint:
    canonical_key: str
    official_code: str
    latitude: float
    longitude: float
    precision_class: str
    source_quality_class: str
    source_coordinate_origin: str
    source_name: str
    source_version: str
    source_reference: str
    coordinate_reference_system: str
    source_record_identity: str
    content_digest: str

    @property
    def map_safe(self) -> bool:
        return (
            self.source_quality_class
            == "OFFICIAL_SOURCE_METHOD"
        )

    @property
    def lon_lat(self) -> tuple[float, float]:
        return (
            self.longitude,
            self.latitude,
        )
