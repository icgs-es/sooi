# SOOI Geography Coordinate Registry V1

Static coordinate authority for Spanish municipality-level map
representation.

Identity authority remains:

`SOOI_GEOGRAPHY_REGISTRY_ES_V3`

Coordinate source:

Instituto Geográfico Nacional / Centro Nacional de Información
Geográfica — Nomenclátor Geográfico de Municipios y Entidades de
Población (NGMEP), edition 2026.

Identity join:

INE municipality code -> SOOI canonical municipality key.

Precision semantics:

`MUNICIPALITY_POPULATION_CENTROID`

This represents the NGMEP municipality population-nucleus reference
centroid. It MUST NOT be presented as:

- an exact property location;
- an address geocode;
- a cadastral parcel;
- a building coordinate;
- the geometric centroid of the municipal polygon.

Product display language must communicate municipality-level
approximation, e.g.:

`Ubicación aproximada a nivel municipal`

Missing canonical identity or missing/non-map-safe coordinate authority
must fail closed.

Attribution for the derived product:

`Obra derivada de NGMEP CC-BY 4.0 ign.es`

No PostGIS dependency is introduced by this registry.

## Map Read Service V1

Contract:

`SOOI_G3_MAP_READ_SERVICE_V1`

The map read service is the explicit bridge:

`PropertyOpportunity.geo_canonical_key`
→ `SOOI_GEOGRAPHY_COORDINATE_REGISTRY_ES_V1`
→ municipality-level map reference point.

It does not persist coordinates on `PropertyOpportunity`.

It does not infer property coordinates.

It does not perform address geocoding.

It does not create points for unresolved geography.

Resolution states are:

- `READY`
- `NO_CANONICAL_IDENTITY`
- `NO_COORDINATE_RECORD`
- `UNSAFE_COORDINATE`

Only `READY` resolutions may be exposed as map points.

Every ready point preserves:

- canonical municipality identity;
- latitude and longitude;
- `MUNICIPALITY_POPULATION_CENTROID` precision;
- municipality-level approximation label;
- source coordinate origin;
- source CRS;
- registry version;
- identity authority;
- NGMEP attribution.

A municipality point is a cartographic reference only and MUST NOT
be represented as the location of the property itself.
