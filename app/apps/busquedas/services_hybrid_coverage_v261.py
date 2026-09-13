from __future__ import annotations

import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.apps import apps
from apps.busquedas.price import normalize_euro_price
from apps.busquedas.provider_gateway import (
    ProviderCircuit,
    ProviderCircuitState,
    ProviderOutcome,
    ProviderResult,
    call_provider,
)
from apps.busquedas.provider_query_provenance import query_provenance


@dataclass(frozen=True)
class SourceSpec:
    slug: str
    domains: tuple[str, ...]
    method: str
    property_scope: str = "any"
    tier: str | None = None
    priority: int = 100


SOURCE_SPECS: list[SourceSpec] = [
    SourceSpec("idealista", ("idealista.com",), "openai_web_search", tier="ai_efficient", priority=10),
    SourceSpec("fotocasa", ("fotocasa.es",), "deterministic", priority=10),
    SourceSpec("habitaclia", ("habitaclia.com",), "deterministic", priority=20),
    SourceSpec("pisos.com", ("pisos.com",), "openai_web_search", tier="ai_efficient", priority=20),
    SourceSpec("yaencontre", ("yaencontre.com",), "openai_web_search", tier="ai_expansion", priority=10),
    SourceSpec("milanuncios", ("milanuncios.com",), "openai_web_search", tier="ai_expansion", priority=20),
    SourceSpec("servihabitat", ("servihabitat.com",), "openai_web_search", tier="ai_expansion", priority=30),
    SourceSpec("solvia", ("solvia.es",), "openai_web_search", tier="ai_expansion", priority=40),
    SourceSpec("altamira", ("altamirainmuebles.com",), "openai_web_search", tier="ai_expansion", priority=50),
    SourceSpec("terrenos.es", ("terrenos.es",), "openai_web_search", property_scope="all", tier="ai_expansion", priority=60),
]


_OPENAI_CIRCUIT_CONTEXT: ContextVar[ProviderCircuit | None] = ContextVar(
    "openai_provider_circuit", default=None
)


UNAVAILABLE_PATTERNS = [
    "la dirección que has introducido no corresponde a ninguna página",
    "la direccion que has introducido no corresponde a ninguna pagina",
    "anuncio no disponible",
    "ya no está publicado",
    "ya no esta publicado",
    "no se encuentra disponible",
    "no está disponible",
    "no esta disponible",
    "página no encontrada",
    "pagina no encontrada",
    "404",
]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ascii_slug(value: str, sep: str = "-") -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", sep, value)
    return value.strip(sep)


def _canonical_query_decimal(value: Any) -> str:
    """Return an accepted context amount without float conversion/exponents."""
    rendered = format(Decimal(str(value)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _get_field(obj: Any, names: list[str], default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val not in (None, "", [], ()):
                return val
    return default


def _profile_context(profile: Any) -> dict[str, Any]:
    location = _get_field(profile, [
        "municipality", "municipio", "city", "locality", "location", "zona",
        "area", "province", "provincia", "address"
    ], "")
    province = _get_field(profile, ["province", "provincia", "region"], "")
    operation = _get_field(profile, [
        "operation", "operation_type", "transaction_type", "search_type", "type"
    ], "")

    all_values = " ".join(
        _clean_text(getattr(profile, f.name, ""))
        for f in profile._meta.fields
        if f.name not in {"created_at", "updated_at"}
    ).lower()

    if not operation:
        if any(x in all_values for x in ["alquiler", "rent"]):
            operation = "alquiler"
        elif any(x in all_values for x in ["venta", "comprar", "sale"]):
            operation = "venta"
        else:
            operation = "alquiler"

    property_blob = " ".join([
        _clean_text(_get_field(profile, ["property_types", "property_type", "tipo_inmueble"], "")),
        all_values,
    ]).lower()

    is_land = any(x in property_blob for x in [
        "terreno", "solar", "parcela", "finca", "suelo", "land", "plot"
    ])

    max_price = _get_field(profile, ["max_price", "price_max", "precio_max", "budget_max"], None)
    min_price = _get_field(profile, ["min_price", "price_min", "precio_min", "budget_min"], None)
    bedrooms = _get_field(profile, ["min_bedrooms", "bedrooms_min", "rooms_min", "habitaciones_min"], None)
    min_area = _get_field(profile, ["min_area_m2", "min_area", "area_min", "m2_min", "min_m2"], None)
    property_types = _get_field(
        profile, ["property_types", "property_type", "tipo_inmueble"], [],
    )
    if isinstance(property_types, str):
        property_types = [value.strip() for value in property_types.split(",") if value.strip()]
    else:
        property_types = list(property_types or [])

    return {
        "location": _clean_text(location),
        "province": _clean_text(province),
        "operation": _clean_text(operation).lower(),
        "is_land": is_land,
        "max_price": max_price,
        "min_price": min_price,
        "bedrooms": bedrooms,
        "min_bedrooms": bedrooms,
        "min_area": min_area,
        "property_types": property_types,
    }


def _is_applicable(spec: SourceSpec, ctx: dict[str, Any]) -> bool:
    if spec.property_scope == "land":
        return bool(ctx.get("is_land"))
    return True


def _domain_matches(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return any(host == d or host.endswith("." + d) for d in domains)


def _normalize_url(url: str) -> str:
    url = _clean_text(url)
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.split("#")[0].strip()


def _extract_urls_from_text(text: str, domains: tuple[str, ...]) -> list[str]:
    urls = re.findall(r"https?://[^\s\"'<>]+", text or "", flags=re.I)
    out = []
    seen = set()
    for url in urls:
        url = _normalize_url(url).rstrip(").,;")
        if not url or url in seen:
            continue
        if _domain_matches(url, domains):
            seen.add(url)
            out.append(url)
    return out


def _strict_candidate_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    # ``candidates`` is the sole documented compatibility wrapper. No prose,
    # markdown fences, alternate keys, or embedded JSON fragments are accepted.
    if isinstance(data, dict) and set(data) == {"candidates"}:
        data = data["candidates"]
    return data if isinstance(data, list) else None


def _items_from_ai_text(
    text: str, spec: SourceSpec, stage_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    data = _strict_candidate_json(text)
    raw_rows = None
    if isinstance(data, list):
        raw_rows = len(data)

    if stage_trace is not None:
        stage_trace["provider_response_present"] = (
            bool(stage_trace.get("provider_response_present")) or bool(text)
        )
        if raw_rows is None:
            stage_trace["provider_raw_item_count_inferable"] = False
            if text:
                stage_trace["format_noncompliance_count"] = (
                    stage_trace.get("format_noncompliance_count", 0) + 1
                )
        else:
            stage_trace.setdefault("provider_raw_item_count_inferable", True)
            stage_trace["provider_raw_item_count"] = (
                stage_trace.get("provider_raw_item_count", 0) + raw_rows
            )

    items: list[dict[str, Any]] = []

    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            url = _normalize_url(row.get("source_url") or row.get("url") or row.get("link") or "")
            if not url or not _domain_matches(url, spec.domains):
                continue
            if spec.slug == "idealista" and _looks_like_listing_url_v261(spec.slug, url):
                if stage_trace is not None:
                    stage_trace["direct_listing_url_rejected"] = int(
                        stage_trace.get("direct_listing_url_rejected", 0)
                    ) + 1
                continue
            items.append({
                "source": spec.slug,
                "source_url": url,
                "title": _clean_text(row.get("title") or row.get("titulo") or ""),
                "price": row.get("price") or row.get("precio"),
                "location": _clean_text(row.get("location") or row.get("ubicacion") or ""),
                "municipality": _clean_text(row.get("municipality") or row.get("municipio") or row.get("city") or row.get("locality") or ""),
                "province": _clean_text(row.get("province") or row.get("provincia") or ""),
                "bedrooms": row.get("bedrooms") or row.get("habitaciones"),
                "area_m2": row.get("area_m2") or row.get("surface") or row.get("superficie"),
                "property_type": _clean_text(row.get("property_type") or row.get("tipo") or ""),
                "summary": _clean_text(row.get("summary") or row.get("descripcion") or row.get("description") or ""),
                "raw": row,
                "provider": "openai_web_search",
            })
            if stage_trace is not None and not _clean_text(row.get("title") or row.get("titulo") or ""):
                stage_trace["missing_title_count"] = int(stage_trace.get("missing_title_count", 0)) + 1

    if stage_trace is not None:
        stage_trace["extracted_candidate_count"] = (
            stage_trace.get("extracted_candidate_count", 0) + len(items)
        )
    return _dedupe_candidates(items)


def _dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for item in items:
        url = _normalize_url(item.get("source_url", ""))
        parsed = urlparse(url)
        identity = (str(item.get("source") or parsed.netloc).lower(), parsed.netloc.lower(), parsed.path.rstrip("/"))
        if not url or identity in seen:
            continue
        seen.add(identity)
        item["source_url"] = url
        out.append(item)
    return out


def _probe_url(url: str, timeout: int = 12) -> dict[str, Any]:
    from .search_availability_semantics import (
        AVAILABLE_CONFIRMED,
        classify_listing_availability,
    )

    result = {
        "http_status": None,
        "final_url": url,
        "html_len": 0,
        "available": False,
        "unavailable_reason": None,
        "error": None,
        "availability_evidence": None,
    }
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 SOOI-V261 QualityProbe",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(350000)
            html = body.decode("utf-8", errors="ignore")
            text = html.lower()
            result["http_status"] = getattr(resp, "status", None)
            result["final_url"] = getattr(resp, "url", url)
            result["html_len"] = len(body)
            # Ephemeral same-response payload for SR0.16C. It is removed before
            # candidate/source evidence is persisted.
            result["_detail_html"] = html

            for pattern in UNAVAILABLE_PATTERNS:
                if pattern in text:
                    result["unavailable_reason"] = pattern
                    break

            result["availability_evidence"] = classify_listing_availability(
                requested_url=url,
                status_code=result["http_status"],
                final_url=result["final_url"],
                html=html,
                explicit_negative=(
                    result["unavailable_reason"]
                    if result["unavailable_reason"] and any(
                        signal in result["unavailable_reason"]
                        for signal in _STRONG_WITHDRAWN_REASONS
                    ) else ""
                ),
            )
            result["available"] = (
                result["availability_evidence"]["state"] == AVAILABLE_CONFIRMED
            )

    except HTTPError as e:
        result["http_status"] = e.code
        try:
            body = e.read(80000)
            html = body.decode("utf-8", errors="ignore")
            text = html.lower()
            result["html_len"] = len(body)
            result["_detail_html"] = html
            for pattern in UNAVAILABLE_PATTERNS:
                if pattern in text:
                    result["unavailable_reason"] = pattern
                    break
        except Exception:
            pass
        result["error"] = f"HTTPError: {e.code}"
        result["availability_evidence"] = classify_listing_availability(
            requested_url=url,
            status_code=result["http_status"],
            final_url=result["final_url"],
            html=locals().get("html", ""),
            error=result["error"],
            explicit_negative=(
                result["unavailable_reason"]
                if result["unavailable_reason"] and any(
                    signal in result["unavailable_reason"]
                    for signal in _STRONG_WITHDRAWN_REASONS
                ) else ""
            ),
        )

    except URLError as e:
        result["error"] = f"URLError: {e.reason}"
        result["availability_evidence"] = classify_listing_availability(
            requested_url=url, status_code=None, final_url=url, html="", error=result["error"],
        )

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["availability_evidence"] = classify_listing_availability(
            requested_url=url, status_code=None, final_url=url, html="", error=result["error"],
        )

    return result


def _deterministic_url(spec: SourceSpec, ctx: dict[str, Any]) -> str:
    loc = ctx.get("location") or ctx.get("province") or ""
    if not loc:
        return ""

    operation = "alquiler" if "alquiler" in ctx.get("operation", "") or "rent" in ctx.get("operation", "") else "venta"

    if spec.slug == "habitaclia":
        params = []
        if ctx.get("bedrooms"):
            params.append(f"hab={ctx['bedrooms']}")
        if ctx.get("min_area"):
            params.append(f"m2={ctx['min_area']}")
        if ctx.get("min_price"):
            params.append(f"pmin={_canonical_query_decimal(ctx['min_price'])}")
        if ctx.get("max_price"):
            params.append(f"pmax={_canonical_query_decimal(ctx['max_price'])}")
        qs = ("?" + "&".join(params)) if params else ""
        return f"https://www.habitaclia.com/{operation}-{_ascii_slug(loc, '_')}.htm{qs}"

    if spec.slug == "fotocasa":
        params = {}
        if ctx.get("bedrooms"):
            params["minRooms"] = ctx["bedrooms"]
        if ctx.get("min_price"):
            params["minPrice"] = _canonical_query_decimal(ctx["min_price"])
        if ctx.get("max_price"):
            params["maxPrice"] = _canonical_query_decimal(ctx["max_price"])
        qs = ("?" + urlencode(params)) if params else ""
        return (
            f"https://www.fotocasa.es/es/{operation}/viviendas/"
            f"{_ascii_slug(loc)}/todas-las-zonas/l{qs}"
        )

    return ""


def _deterministic_candidates(
    spec: SourceSpec, ctx: dict[str, Any], timeout: int,
    provenance: list[dict[str, Any]] | None = None,
    request_trace: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    url = _deterministic_url(spec, ctx)
    if not url:
        return [], "no_location_for_deterministic_url"

    try:
        from apps.busquedas.services_portal_extractors import probe_portal_url
    except Exception as e:
        return [], f"extractor_import_error: {type(e).__name__}: {e}"

    try:
        res = probe_portal_url(spec.slug, url, municipality=ctx.get("location") or "", timeout=timeout)
        if request_trace is not None and isinstance(res.get("result_telemetry"), dict):
            request_trace.append({
                "provider": spec.slug,
                "geography": str(ctx.get("location") or ctx.get("province") or "unknown"),
                **res["result_telemetry"],
            })
        if provenance is not None and isinstance(res.get("query_provenance"), dict):
            item = dict(res["query_provenance"])
            item["planned"] = {
                **item.get("planned", {}),
                "operation": _canonical_operation(ctx.get("operation")),
                "geography": str(ctx.get("location") or ctx.get("province") or "unknown"),
            }
            provenance.append(item)
        candidates = (
            res.get("candidates")
            or res.get("items")
            or res.get("results")
            or res.get("extracted", {}).get("candidates")
            or []
        )
        out = []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            source_url = _normalize_url(row.get("source_url") or row.get("url") or row.get("link") or "")
            if not source_url or not _domain_matches(source_url, spec.domains):
                continue
            out.append({
                "source": spec.slug,
                "source_url": source_url,
                "title": _clean_text(row.get("title") or row.get("titulo") or ""),
                "price": row.get("price") or row.get("precio"),
                "location": _clean_text(row.get("location") or row.get("ubicacion") or ""),
                "municipality": _clean_text(row.get("municipality") or row.get("municipio") or row.get("city") or row.get("locality") or ""),
                "province": _clean_text(row.get("province") or row.get("provincia") or ""),
                "bedrooms": row.get("bedrooms") or row.get("habitaciones"),
                "area_m2": row.get("area_m2") or row.get("surface") or row.get("superficie"),
                "property_type": _clean_text(row.get("property_type") or row.get("tipo") or ""),
                "summary": _clean_text(row.get("summary") or row.get("description") or ""),
                "raw": row,
                "provider": "deterministic",
            })
        return _dedupe_candidates(out), None
    except Exception as e:
        return [], f"deterministic_error: {type(e).__name__}: {e}"



def _source_url_rules_v2616(spec: SourceSpec) -> str:
    slug = str(spec.slug or "").lower()

    if slug == "idealista":
        return "Regla URL Idealista: solo fichas individuales con patrón /inmueble/<id>/; nunca /alquiler-viviendas/, /geo/, /buscar/ ni listados."

    if slug == "pisos.com":
        return "Regla URL pisos.com: prioriza fichas individuales de alquiler con patrón /alquilar/...-<id>_<id>/; nunca listados, mapas ni búsquedas."

    if slug == "yaencontre":
        return "Regla URL yaencontre: solo fichas individuales con /inmueble-...; nunca páginas de resultados ni categorías."

    if slug == "milanuncios":
        return "Regla URL Milanuncios: solo anuncios individuales con ID numérico, normalmente terminados en -<id>.htm; nunca páginas /precio/, /larga-temporada.htm, /adosados.htm, categorías ni listados."

    if slug == "terrenos.es":
        return "Regla URL terrenos.es: solo ficha individual concreta de inmueble/terreno; debe incluir precio si está disponible; nunca listados ni páginas de municipio."

    if slug in {"servihabitat", "solvia", "altamira"}:
        return "Regla URL servicer/banca: solo ficha individual concreta de inmueble; nunca páginas de búsqueda, promociones genéricas ni listados."

    return "Regla URL: prioriza ficha individual concreta de inmueble; nunca listados ni páginas genéricas."


def _contract_scalar(value: Any) -> str:
    if value in (None, ""):
        return "null"
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else format(number, "g")
    except (TypeError, ValueError):
        return str(value).strip()


def _canonical_operation(value: Any) -> str:
    return "rent" if str(value or "").strip().lower() in {"rent", "alquiler"} else "sale"


def _structured_constraint_block(ctx: dict[str, Any]) -> str:
    locations = [str(value).strip() for value in (ctx.get("search_locations") or []) if value]
    if not locations:
        location = ctx.get("location") or ctx.get("province")
        locations = [str(location).strip()] if location else []
    property_types = [str(value).strip() for value in (ctx.get("property_types") or []) if value]
    return "\n".join((
        "SOOI_SEARCH_CONSTRAINTS_V1",
        f"operation={_canonical_operation(ctx.get('operation'))}",
        f"min_price={_contract_scalar(ctx.get('min_price'))}",
        f"max_price={_contract_scalar(ctx.get('max_price'))}",
        f"min_bedrooms={_contract_scalar(ctx.get('min_bedrooms', ctx.get('bedrooms')))}",
        f"property_types={','.join(property_types) if property_types else 'any'}",
        f"bounded_municipalities={'|'.join(locations)}",
        "END_SOOI_SEARCH_CONSTRAINTS_V1",
    ))


DEFAULT_EXTERNAL_SOURCE_CAP = 10
IDEALISTA_EXTERNAL_RESULT_CAP = 30
IDEALISTA_QUERY_PLAN_MAX_GROUPS = 4


def _external_result_cap(spec: SourceSpec, requested: int) -> int:
    """Bound external-source output without changing deterministic providers."""
    requested_cap = max(1, int(requested or DEFAULT_EXTERNAL_SOURCE_CAP))
    if spec.slug == "idealista" and spec.method == "openai_web_search":
        return IDEALISTA_EXTERNAL_RESULT_CAP
    if spec.method == "openai_web_search":
        return min(DEFAULT_EXTERNAL_SOURCE_CAP, requested_cap)
    return requested_cap


def _idealista_query_groups(ctx: dict[str, Any], max_groups: int = IDEALISTA_QUERY_PLAN_MAX_GROUPS) -> list[list[str]]:
    locations = [str(value).strip() for value in (ctx.get("search_locations") or []) if str(value).strip()]
    if not locations:
        location = str(ctx.get("location") or ctx.get("province") or "").strip()
        locations = [location] if location else []
    count = min(max(1, int(max_groups)), len(locations)) if locations else 0
    groups = [[] for _ in range(count)]
    for index, location in enumerate(locations):
        groups[index % count].append(location)
    return groups


_OPENAI_CANDIDATE_TEXT_CONFIG = {
    "format": {
        "type": "json_schema",
        "name": "property_candidates",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "source_url": {"type": "string"},
                            "title": {"type": "string"},
                            "price": {"type": ["number", "string"]},
                            "municipality": {"type": "string"},
                            "bedrooms": {"type": "number"},
                            "property_type": {
                                "type": "string",
                                "enum": ["house", "flat", "land", "commercial"],
                            },
                            "area_m2": {"type": ["number", "null"]},
                        },
                        "required": [
                            "source_url", "title", "price", "municipality", "bedrooms",
                            "property_type", "area_m2",
                        ],
                    },
                },
            },
            "required": ["candidates"],
        },
    },
}

def _openai_prompt(spec: SourceSpec, ctx: dict[str, Any], max_results: int) -> str:
    if spec.slug == "idealista":
        domains = f"{', '.join(spec.domains)} — devuelve solo URLs de este dominio si las encuentras"
        domain_header = "Dominio objetivo (preferido, no restrictivo):"
        domain_rule = "Devuelve solo URLs de idealista.com. Si no encuentras fichas reales de idealista, devuelve []."
    else:
        domains = " OR ".join([f"site:{d}" for d in spec.domains])
        domain_header = "Restricción obligatoria de dominio:"
        domain_rule = "Devuelve SOLO resultados del dominio indicado."
    location = ctx.get("location") or ctx.get("province") or "España"
    operation = ctx.get("operation") or "alquiler"

    filters = []
    if ctx.get("min_price"):
        filters.append(f"precio mínimo {ctx['min_price']}")
    if ctx.get("max_price"):
        filters.append(f"precio máximo {ctx['max_price']}")
    if ctx.get("bedrooms"):
        filters.append(f"habitaciones mínimas {ctx['bedrooms']}")
    if ctx.get("min_area"):
        filters.append(f"superficie mínima {ctx['min_area']} m2")
    property_types = [str(value) for value in (ctx.get("property_types") or []) if value]
    if property_types:
        filters.append(f"tipos de inmueble {', '.join(property_types)}")

    filter_text = ", ".join(filters) if filters else "sin filtros adicionales claros"
    source_rules = _source_url_rules_v2616(spec)
    constraint_block = _structured_constraint_block(ctx)

    search_locations = ctx.get("search_locations") or []
    if search_locations and len(search_locations) > 1:
        location_instruction = (
            f"Operación exacta: {operation}. Usa exclusivamente los municipios autorizados "
            "del bloque SOOI_SEARCH_CONSTRAINTS_V1 para este lote."
        )
    else:
        location_instruction = f"{operation} vivienda/inmueble en {location}."

    query_plan_instruction = ""
    if spec.slug == "idealista":
        groups = _idealista_query_groups(ctx)
        query_plan_instruction = (
            "Plan geográfico interno acotado (una sola expansión SOOI): "
            + " | ".join(f"grupo {index + 1}: {', '.join(group)}" for index, group in enumerate(groups))
            + ". Busca de forma distribuida entre todos los grupos; no concentres la salida en una sola localidad."
        )

    return f"""
Busca anuncios inmobiliarios reales y actuales exclusivamente en {spec.slug}.

{domain_header}
{domains}

Consulta:
{location_instruction} Filtros: {filter_text}.
{query_plan_instruction}

Restricciones autoritativas (valores exactos de máquina):
{constraint_block}

Reglas:
- {domain_rule}
- No inventes URLs.
- Prioriza fichas individuales reales de inmueble frente a listados.\n- {source_rules}
- No devuelvas páginas de búsqueda genéricas si puedes devolver fichas individuales.
- En búsquedas provinciales, reparte resultados entre municipios si existen candidatos fiables.
- municipality debe ser el municipio/localidad explícito del anuncio, nunca la provincia, el dominio, la URL ni la consulta usada para encontrarlo. Usa null si no hay evidencia explícita.
- Conserva en location el texto mostrado, incluidos barrios o distritos; no lo uses como sustituto de municipality.
- Cada candidato debe satisfacer TODOS los filtros duros indicados: operación, municipio
  autorizado, precio mínimo/máximo cuando exista, dormitorios mínimos, tipos de inmueble
  permitidos y superficie mínima cuando exista.
- Devuelve exclusivamente URLs de fichas individuales/detalle exactas. No incluyas a sabiendas
  ningún candidato fuera de rango ni con un filtro duro desconocido.
- Si no encuentras candidatos fiables que satisfagan TODOS los filtros, devuelve [].
- Este lote es una consulta acotada; no afirmes cobertura completa del mercado ni de la geografía.
- Máximo {max_results} resultados.
- Devuelve SOLO el objeto JSON conforme al contrato; cero resultados es {{"candidates": []}}. Sin markdown,
  comentarios, explicación, prefijo ni sufijo.
- Formato estricto JSON:

{{"candidates": [
  {{
    "title": "título breve del anuncio",
    "source_url": "https://...",
    "price": "number or numeric string",
    "municipality": "explicit municipality/locality string",
    "bedrooms": "number",
    "area_m2": "number or null",
    "property_type": "house, flat, land, or commercial"
  }}
]}}
""".strip()


def _call_openai_web_search(
    prompt: str, circuit: ProviderCircuit | None = None,
    provenance: list[dict[str, Any]] | None = None,
    source_provider: str = "unknown",
) -> ProviderResult[str]:
    circuit = circuit or _OPENAI_CIRCUIT_CONTEXT.get() or ProviderCircuit("openai")
    if circuit.state.value == "OPEN":
        return call_provider(circuit, lambda: "")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        circuit.open("OPENAI_API_KEY_not_configured")
        return ProviderResult(ProviderOutcome.HARD_FAILURE, reason=circuit.reason)

    try:
        from openai import OpenAI
    except Exception as e:
        circuit.open(f"openai_import_error: {type(e).__name__}: {e}")
        return ProviderResult(ProviderOutcome.HARD_FAILURE, reason=circuit.reason)

    model = (
        os.environ.get("SOOI_OPENAI_WEB_SEARCH_MODEL")
        or os.environ.get("OPENAI_WEB_SEARCH_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4.1-mini"
    )

    client_result = call_provider(circuit, lambda: OpenAI(api_key=api_key, max_retries=0))
    if client_result.outcome is not ProviderOutcome.SUCCESS:
        return client_result
    client = client_result.value

    last_result: ProviderResult[str] | None = None
    for tool_type in ["web_search", "web_search_preview"]:
        def execute_openai_request(tool_type=tool_type):
            # Closest observable SDK boundary. HTTP status and final location
            # are not exposed here, so those response fields remain unknown.
            if provenance is not None:
                provenance.append(query_provenance(
                    source_provider, "", "https://api.openai.com/v1/responses",
                    operation="web_search", geography="provider_tool",
                ))
            return client.responses.create(
                model=model,
                input=prompt,
                tools=[{"type": tool_type}],
                text=_OPENAI_CANDIDATE_TEXT_CONFIG,
            )
        result = call_provider(
            circuit,
            execute_openai_request,
        )
        if result.outcome is ProviderOutcome.SUCCESS:
            response = result.value
            text = getattr(response, "output_text", "") or ""
            return ProviderResult(ProviderOutcome.SUCCESS, value=text)
        last_result = result
        if result.outcome in (ProviderOutcome.HARD_FAILURE, ProviderOutcome.SKIPPED_CIRCUIT):
            return result
        reason = str(result.reason or "").lower().replace("-", "_")
        if tool_type == "web_search" and not any(marker in reason for marker in (
            "invalid value: 'web_search'", 'invalid value: "web_search"',
            "unsupported tool variant", "unknown tool type web_search",
        )):
            return result

    return last_result or ProviderResult(
        ProviderOutcome.RETRYABLE_FAILURE, reason="openai_web_search_failed"
    )



def _to_float(value: Any) -> float | None:
    normalized = normalize_euro_price(value)
    return float(normalized) if normalized is not None else None


def _candidate_constraint_violations(ctx: dict[str, Any], item: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    price = _to_float(item.get("price"))
    max_price = _to_float(ctx.get("max_price"))
    min_price = _to_float(ctx.get("min_price"))

    if price is not None and max_price is not None and price > max_price:
        violations.append(f"price_above_max:{price:g}>{max_price:g}")

    if price is not None and min_price is not None and price < min_price:
        violations.append(f"price_below_min:{price:g}<{min_price:g}")

    if price is not None and price < 10:
        violations.append(f"price_implausible:{price:g}")

    bedrooms = _to_float(item.get("bedrooms"))
    min_bedrooms = _to_float(ctx.get("bedrooms"))
    if bedrooms is not None and min_bedrooms is not None and bedrooms < min_bedrooms:
        violations.append(f"bedrooms_below_min:{bedrooms:g}<{min_bedrooms:g}")

    area = _to_float(item.get("area_m2"))
    min_area = _to_float(ctx.get("min_area"))
    if area is not None and min_area is not None and area < min_area:
        violations.append(f"area_below_min:{area:g}<{min_area:g}")

    wanted_types = {_ascii_slug(value) for value in (ctx.get("property_types") or []) if value}
    candidate_type = _ascii_slug(str(item.get("property_type") or ""))
    type_aliases = {
        "casa": "house", "chalet": "house", "house": "house",
        "piso": "flat", "apartamento": "flat", "flat": "flat",
        "terreno": "land", "parcela": "land", "land": "land",
        "local": "commercial", "commercial": "commercial",
    }
    if candidate_type and wanted_types:
        normalized_wanted = {type_aliases.get(value, value) for value in wanted_types}
        normalized_candidate = type_aliases.get(candidate_type, candidate_type)
        if normalized_candidate not in normalized_wanted:
            violations.append(f"property_type_mismatch:{candidate_type}")

    if ctx.get("location_scope") in {"multi_location", "comarca", "municipality"}:
        wanted_locations = [
            _norm_place_text(value) for value in (ctx.get("search_locations") or []) if value
        ]
        if not wanted_locations and ctx.get("location"):
            wanted_locations = [_norm_place_text(ctx.get("location"))]
        municipality = _norm_place_text(item.get("municipality"))
        intent_rows = ctx.get("intent_resolutions") or []
        if intent_rows:
            from .geography_runtime import candidate_location_match
            comparison = candidate_location_match(intent_rows, item.get("municipality"))
            if comparison == "MISMATCH":
                violations.append("location_outside_search_scope")
            elif comparison == "LEGACY" and wanted_locations and municipality not in wanted_locations:
                violations.append("location_outside_search_scope")
        elif wanted_locations and municipality and municipality not in wanted_locations:
            violations.append("location_outside_search_scope")

    return violations


def _candidate_constraint_unknowns(ctx: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """Return constrained attributes for which neither match nor mismatch is provable."""
    unknowns: list[str] = []

    if (ctx.get("min_price") not in (None, "") or ctx.get("max_price") not in (None, "")) \
            and _to_float(item.get("price")) is None:
        unknowns.append("unknown_required_attribute:price")

    if ctx.get("bedrooms") not in (None, "") and _to_float(item.get("bedrooms")) is None:
        unknowns.append("unknown_required_attribute:bedrooms")

    if ctx.get("min_area") not in (None, "") and _to_float(item.get("area_m2")) is None:
        unknowns.append("unknown_required_attribute:area_m2")

    wanted_types = [value for value in (ctx.get("property_types") or []) if value]
    if wanted_types and not _ascii_slug(str(item.get("property_type") or "")):
        unknowns.append("unknown_required_attribute:property_type")

    if ctx.get("location_scope") in {"multi_location", "comarca", "municipality"}:
        wanted_locations = [
            _norm_place_text(value) for value in (ctx.get("search_locations") or []) if value
        ]
        if not wanted_locations and ctx.get("location"):
            wanted_locations = [_norm_place_text(ctx.get("location"))]
        if wanted_locations and not _norm_place_text(item.get("municipality")):
            unknowns.append("unknown_required_attribute:location")

    return unknowns


_STRONG_WITHDRAWN_REASONS = (
    "ya no está publicado", "ya no esta publicado", "lo dio de baja", "dado de baja",
    "retirado", "withdrawn", "deleted",
)


def _classify_probe_outcome(probe: dict[str, Any]) -> tuple[str, str]:
    """Classify availability evidence as MATCH, MISMATCH, or UNKNOWN."""
    evidence = probe.get("availability_evidence")
    if isinstance(evidence, dict):
        state = str(evidence.get("state") or "").lower()
        signal = str(evidence.get("signal") or "inconclusive").lower()
        if state == "unavailable":
            return "MISMATCH", f"availability_unavailable:{signal}"
        if state == "confirmed":
            return "MATCH", f"availability_match:{signal}"
        return "UNKNOWN", f"availability_unknown:{signal}"

    status = probe.get("http_status")
    try:
        status_i = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_i = None

    if status_i in {404, 410}:
        return "MISMATCH", f"availability_unavailable:http_{status_i}"

    reason = str(probe.get("unavailable_reason") or "").strip().lower()
    if reason and any(signal in reason for signal in _STRONG_WITHDRAWN_REASONS):
        return "MISMATCH", f"availability_unavailable:{reason}"

    if status_i in {408, 425, 429}:
        return "UNKNOWN", f"availability_unknown:http_{status_i}"
    if status_i in {401, 403}:
        return "UNKNOWN", f"availability_unknown:http_{status_i}"
    if status_i is not None and 500 <= status_i <= 599:
        return "UNKNOWN", "availability_unknown:http_5xx"

    error = str(probe.get("error") or "").lower()
    if error:
        if "timeout" in error or "timed out" in error:
            return "UNKNOWN", "availability_unknown:timeout"
        return "UNKNOWN", "availability_unknown:network"

    if reason:
        return "UNKNOWN", "availability_unknown:http_200_ambiguous_text"
    if probe.get("available") is True:
        return "MATCH", "availability_match:probe_available"
    return "UNKNOWN", "availability_unknown:inconclusive"

def _candidate_detail_url_is_valid(spec: SourceSpec, url: str) -> bool:
    """Accept only provider-owned URLs already proven to be listing details."""
    if not url or not _domain_matches(url, spec.domains):
        return False
    if spec.slug not in {"fotocasa", "habitaclia"}:
        return True
    from .services_portal_extractors import (
        is_fotocasa_listing_url,
        is_habitaclia_listing_url,
    )
    validator = is_fotocasa_listing_url if spec.slug == "fotocasa" else is_habitaclia_listing_url
    return bool(validator(url))


def _availability_probe_metadata(*, attempted: bool, provider: str, probe=None,
                                 outcome: str = "not_eligible", signal: str = "") -> dict[str, Any]:
    probe = probe if isinstance(probe, dict) else {}
    evidence = probe.get("availability_evidence")
    if isinstance(evidence, dict):
        outcome = str(evidence.get("state") or outcome)
        signal = str(evidence.get("signal") or signal)
    status = probe.get("http_status")
    try:
        status_class = f"{int(status) // 100}xx" if status is not None else "unknown"
    except (TypeError, ValueError):
        status_class = "unknown"
    return {
        "attempted": bool(attempted),
        "provider": provider,
        "outcome": outcome,
        "status_class": status_class,
        "signal": signal or ("not_eligible" if not attempted else "inconclusive"),
    }


def _unknown_only_detail_enrichment(
    verdict: dict[str, Any], *, html: str, requested_url: str, final_url: str,
    allow_fotocasa_initial_props: bool = True,
) -> dict[str, Any]:
    from .services_portal_extractors import (
        extract_fotocasa_initial_props_detail_attributes,
        extract_same_listing_detail_attributes,
    )

    jsonld = extract_same_listing_detail_attributes(html, requested_url, final_url)
    initial = (
        extract_fotocasa_initial_props_detail_attributes(html, requested_url, final_url)
        if allow_fotocasa_initial_props else
        {"identity_matched": False, "attributes": {}, "conflicts": {}}
    )
    jsonld_attributes = jsonld.get("attributes") if jsonld.get("identity_matched") else {}
    initial_attributes = initial.get("attributes") if initial.get("identity_matched") else {}
    jsonld_attributes = jsonld_attributes if isinstance(jsonld_attributes, dict) else {}
    initial_attributes = initial_attributes if isinstance(initial_attributes, dict) else {}
    initial_conflicts = initial.get("conflicts") if isinstance(initial.get("conflicts"), dict) else {}
    evidence: dict[str, dict[str, Any]] = {}
    conflict = False

    mappings = {
        "price": ("price",),
        "bedrooms": ("bedrooms",),
        "property_type": ("property_type",),
        "location": ("municipality", "location"),
        "province": ("province",),
    }
    for evidence_key, target_fields in mappings.items():
        source_key = "municipality" if evidence_key == "location" else evidence_key
        if jsonld_attributes.get(source_key) not in (None, ""):
            detail_value = jsonld_attributes[source_key]
            source = "jsonld"
            extraction_conflict = False
        else:
            detail_value = initial_attributes.get(source_key)
            source = "fotocasa_initial_props" if (
                detail_value not in (None, "") or initial_conflicts.get(source_key)
            ) else "unknown"
            extraction_conflict = bool(initial_conflicts.get(source_key))
        filled = False
        field_conflict = extraction_conflict
        if detail_value not in (None, ""):
            known_values = [verdict.get(field) for field in target_fields if verdict.get(field) not in (None, "")]
            if known_values:
                field_conflict = field_conflict or any(
                    str(value).strip().casefold() != str(detail_value).strip().casefold()
                    for value in known_values
                )
            else:
                for field in target_fields:
                    verdict[field] = detail_value
                filled = True
        conflict = conflict or field_conflict
        evidence[evidence_key] = {
            "source": source,
            "filled": filled,
            "conflict": field_conflict,
        }

    return {
        "identity_matched": bool(jsonld.get("identity_matched") or initial.get("identity_matched")),
        "identity_sources": {
            "jsonld": bool(jsonld.get("identity_matched")),
            "fotocasa_initial_props": bool(initial.get("identity_matched")),
        },
        "fields": evidence,
        "detail_attribute_conflict": conflict,
    }


def _classify_candidate(spec: SourceSpec, item: dict[str, Any], ctx: dict[str, Any], timeout: int,
                        *, probe_cache: dict[str, dict[str, Any]] | None = None,
                        allow_detail_probe: bool = True,
                        targeted_detail: bool = False) -> dict[str, Any]:
    url = item["source_url"]
    verdict = {
        "url": url,
        "title": item.get("title") or "",
        "price": item.get("price"),
        "location": item.get("location") or "",
        "municipality": item.get("municipality") or "",
        "province": item.get("province") or "",
        "bedrooms": item.get("bedrooms"),
        "area_m2": item.get("area_m2"),
        "property_type": item.get("property_type") or "",
        "provider": item.get("provider"),
        "search_location": item.get("search_location"),
        "search_location_batch": item.get("search_location_batch"),
        "classification": "reviewable",
        "reason": "default_reviewable",
        "probe": {},
        "availability_evidence": None,
    }

    violations = _candidate_constraint_violations(ctx, item)
    if violations and targeted_detail:
        verdict["classification"] = "discarded"
        verdict["reason"] = ";".join(violations)
        verdict["hard_violations"] = violations
        verdict["availability_probe"] = _availability_probe_metadata(
            attempted=False, provider=spec.slug, signal="hard_rejected",
        )
        return verdict

    if targeted_detail and (not allow_detail_probe or not _candidate_detail_url_is_valid(spec, url)):
        verdict["classification"] = "reviewable"
        verdict["reason"] = "availability_unknown:detail_url_required"
        verdict["availability_probe"] = _availability_probe_metadata(
            attempted=False, provider=spec.slug, outcome="unknown", signal="detail_url_required",
        )
        return verdict

    cache_key = _normalize_url(url)
    deduplicated = probe_cache is not None and cache_key in probe_cache
    if deduplicated:
        probe = probe_cache[cache_key]
    else:
        probe = _probe_url(url, timeout=timeout)
        if probe_cache is not None:
            probe_cache[cache_key] = probe
    evidence = probe.get("availability_evidence")
    if (
        isinstance(evidence, dict)
        and evidence.get("state") == "confirmed"
        and not _domain_matches(str(probe.get("final_url") or ""), spec.domains)
    ):
        probe = dict(probe)
        probe["available"] = False
        probe["availability_evidence"] = {
            "state": "unknown",
            "signal": "unexpected_final_domain",
            "source": "redirect",
        }
        if probe_cache is not None:
            probe_cache[cache_key] = probe
    if targeted_detail:
        final_url = str(probe.get("final_url") or url)
        verdict["detail_attribute_evidence"] = _unknown_only_detail_enrichment(
            verdict,
            html=(
                str(probe.get("_detail_html") or "")
                if _domain_matches(final_url, spec.domains) else ""
            ),
            requested_url=url,
            final_url=final_url,
            allow_fotocasa_initial_props=(
                spec.slug == "fotocasa"
                and str((probe.get("availability_evidence") or {}).get("state") or "") != "unavailable"
            ),
        )
    persisted_probe = {key: value for key, value in probe.items() if key != "_detail_html"}
    verdict["probe"] = persisted_probe
    verdict["availability_evidence"] = probe.get("availability_evidence")
    verdict["availability_probe"] = _availability_probe_metadata(
        attempted=not deduplicated, provider=spec.slug, probe=probe,
        signal="deduplicated" if deduplicated else "",
    )
    verdict["availability_probe"]["deduplicated"] = deduplicated

    violations = _candidate_constraint_violations(ctx, verdict)
    if violations:
        verdict["classification"] = "discarded"
        verdict["reason"] = ";".join(violations)
        verdict["hard_violations"] = violations
        return verdict

    availability_state, availability_reason = _classify_probe_outcome(probe)
    if availability_state == "MISMATCH":
        verdict["classification"] = "discarded"
        verdict["reason"] = availability_reason
        return verdict

    unknowns = _candidate_constraint_unknowns(ctx, verdict)
    if unknowns or availability_state == "UNKNOWN":
        verdict["classification"] = "reviewable"
        verdict["reason"] = ";".join(unknowns + ([availability_reason] if availability_state == "UNKNOWN" else []))
        return verdict

    if spec.slug == "idealista":
        verdict["classification"] = "reviewable"
        verdict["reason"] = "idealista_never_auto_captured_without_strong_validation"
        return verdict

    if item.get("provider") == "deterministic" and availability_state == "MATCH":
        verdict["classification"] = "verified"
        verdict["reason"] = "deterministic_candidate_probe_available"
        return verdict

    if availability_state == "MATCH":
        verdict["classification"] = "reviewable"
        verdict["reason"] = "ai_candidate_probe_available"
        return verdict

    verdict["classification"] = "reviewable"
    verdict["reason"] = "not_strong_enough_to_verify_but_not_discarded"
    return verdict


def _detail_verification_counters(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "detail_verification_eligible": 0,
        "detail_verification_attempted": 0,
        "detail_verification_confirmed": 0,
        "detail_verification_unknown": 0,
        "detail_verification_unavailable": 0,
        "detail_verification_deduplicated": 0,
    }
    for candidate in candidates:
        metadata = candidate.get("availability_probe") or {}
        if metadata.get("signal") != "hard_rejected":
            counters["detail_verification_eligible"] += 1
        if metadata.get("attempted"):
            counters["detail_verification_attempted"] += 1
        if metadata.get("deduplicated"):
            counters["detail_verification_deduplicated"] += 1
        outcome = str(metadata.get("outcome") or "")
        key = f"detail_verification_{outcome}"
        if key in counters:
            counters[key] += 1
    return counters


def _classify_targeted_detail_candidates(
    spec: SourceSpec, items: list[dict[str, Any]], ctx: dict[str, Any], timeout: int,
    *, probe_cache: dict[str, dict[str, Any]], cap: int,
) -> list[dict[str, Any]]:
    """Bounded, at-most-once detail verification for deterministic listings."""
    verdicts: list[dict[str, Any]] = []
    attempted = 0
    for item in items:
        verdict = _classify_candidate(
            spec, item, ctx=ctx, timeout=timeout, probe_cache=probe_cache,
            allow_detail_probe=attempted < max(0, int(cap)), targeted_detail=True,
        )
        if (verdict.get("availability_probe") or {}).get("attempted"):
            attempted += 1
        verdicts.append(verdict)
    return verdicts


def _coverage_row(spec: SourceSpec, attempted: bool, method: str, status: str, error: str | None = None) -> dict[str, Any]:
    return {
        "source": spec.slug,
        "attempted": attempted,
        "method": method,
        "status": status,
        "candidate_count": 0,
        "verified": 0,
        "reviewable": 0,
        "discarded": 0,
        "error": error,
        "candidates": [],
        "query_provenance": [],
        "deterministic_request_telemetry": [],
        "stage_counters": {
            "provider_response_present": False,
            "provider_raw_item_count": "UNKNOWN",
            "extracted_candidate_count": 0,
            "normalized_or_deduped_candidate_count": 0,
            "accepted_candidate_count": 0,
            "rejected_geography_count": 0,
            "rejected_price_count": 0,
            "rejected_bedroom_count": 0,
            "rejected_property_type_count": 0,
            "rejected_missing_evidence_count": 0,
            "format_noncompliance_count": 0,
        },
    }



def _captured_property_url_field() -> str | None:
    CapturedProperty = apps.get_model("inmuebles", "CapturedProperty")
    for name in ["source_url", "url", "external_url"]:
        try:
            CapturedProperty._meta.get_field(name)
            return name
        except Exception:
            continue
    return None


def _canonical_url_for_duplicate(url: str) -> str:
    try:
        from apps.busquedas.services import _normalize_property_url
        return _normalize_property_url(url)
    except Exception:
        return _normalize_url(url)


def _external_id_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if "fotocasa.es" in host:
        m = re.search(r"/(\d+)/d/?$", path)
        if m:
            return f"fotocasa:{m.group(1)}"

    if "habitaclia.com" in host:
        m = re.search(r"-i(\d+)\.htm$", path)
        if m:
            return f"habitaclia:{m.group(1)}"

    if "idealista.com" in host:
        m = re.search(r"/inmueble/(\d+)/?", path)
        if m:
            return f"idealista:{m.group(1)}"

    if "pisos.com" in host:
        m = re.search(r"-(\d+_\d+)/?", path)
        if m:
            return f"pisos:{m.group(1)}"

    return ""




def _external_id_variants_from_url(url: str) -> list[str]:
    external_id = _external_id_from_url(url)
    variants: list[str] = []

    def add(value: str | None):
        value = str(value or "").strip()
        if value and value not in variants:
            variants.append(value)

    add(external_id)

    if ":" in external_id:
        raw = external_id.split(":", 1)[-1]
        add(raw)

        # Compatibilidad con legacy: a veces source_external_id guardó URL completa.
        parsed_url = str(url or "").strip()
        add(parsed_url)
        add(_canonical_url_for_duplicate(parsed_url))
        add(_normalize_url(parsed_url))

    return variants


def _captured_property_duplicate_id_for_url(url: str) -> int | None:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None

    try:
        from django.apps import apps
        CapturedProperty = apps.get_model("inmuebles", "CapturedProperty")
    except Exception:
        return None

    url_variants: list[str] = []

    def add_url(value: str | None):
        value = str(value or "").strip()
        if value and value not in url_variants:
            url_variants.append(value)

    add_url(raw_url)
    add_url(_canonical_url_for_duplicate(raw_url))
    add_url(_normalize_url(raw_url))

    for value in url_variants:
        obj = (
            CapturedProperty.objects
            .filter(source_url=value)
            .only("id")
            .order_by("-id")
            .first()
        )
        if obj:
            return obj.id

    for value in _external_id_variants_from_url(raw_url):
        obj = (
            CapturedProperty.objects
            .filter(source_external_id=value)
            .only("id")
            .order_by("-id")
            .first()
        )
        if obj:
            return obj.id

    external_id = _external_id_from_url(raw_url)
    raw_external_number = external_id.split(":", 1)[-1] if external_id else ""
    if raw_external_number:
        qs = (
            CapturedProperty.objects
            .filter(source_url__icontains=raw_external_number)
            .only("id", "source_url")
            .order_by("-id")[:20]
        )
        for obj in qs:
            try:
                if _external_id_from_url(obj.source_url) == external_id:
                    return obj.id
            except Exception:
                continue

    return None


def _opportunity_duplicate_capture_id_for_url(url: str) -> int | None:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None

    try:
        from django.apps import apps
        PropertyOpportunity = apps.get_model("seguimiento", "PropertyOpportunity")
    except Exception:
        return None

    url_variants: list[str] = []

    def add_url(value: str | None):
        value = str(value or "").strip()
        if value and value not in url_variants:
            url_variants.append(value)

    add_url(raw_url)
    add_url(_canonical_url_for_duplicate(raw_url))
    add_url(_normalize_url(raw_url))

    for value in url_variants:
        obj = (
            PropertyOpportunity.objects
            .filter(captured_property__source_url=value)
            .select_related("captured_property")
            .only("captured_property_id")
            .order_by("-id")
            .first()
        )
        if obj:
            return obj.captured_property_id

    for value in _external_id_variants_from_url(raw_url):
        obj = (
            PropertyOpportunity.objects
            .filter(captured_property__source_external_id=value)
            .select_related("captured_property")
            .only("captured_property_id")
            .order_by("-id")
            .first()
        )
        if obj:
            return obj.captured_property_id

    external_id = _external_id_from_url(raw_url)
    raw_external_number = external_id.split(":", 1)[-1] if external_id else ""
    if raw_external_number:
        qs = (
            PropertyOpportunity.objects
            .filter(captured_property__source_url__icontains=raw_external_number)
            .select_related("captured_property")
            .only("captured_property_id", "captured_property__source_url")
            .order_by("-id")[:20]
        )
        for obj in qs:
            try:
                if _external_id_from_url(obj.captured_property.source_url) == external_id:
                    return obj.captured_property_id
            except Exception:
                continue

    return None


def _existing_capture_id_for_url(url: str) -> int | None:
    """
    Anti-duplicado V2.6.1.3:
    - CapturedProperty existente.
    - PropertyOpportunity ya convertida, vía captured_property.
    Devuelve siempre el captured_property_id cuando existe.
    """
    return (
        _captured_property_duplicate_id_for_url(url)
        or _opportunity_duplicate_capture_id_for_url(url)
    )



def _capture_has_property_opportunity_v261(capture_id: int | None) -> bool:
    if not capture_id:
        return False
    try:
        from django.apps import apps
        PropertyOpportunity = apps.get_model("seguimiento", "PropertyOpportunity")
        return PropertyOpportunity.objects.filter(captured_property_id=capture_id).exists()
    except Exception:
        return False

def _build_action_plan(source_coverage: list[dict[str, Any]]) -> dict[str, Any]:
    from .search_quality_semantics import result_actionability

    actions: list[dict[str, Any]] = []
    totals: dict[str, int] = {}

    for source_row in source_coverage:
        source = source_row.get("source")
        for item in source_row.get("candidates") or []:
            url = item.get("url") or ""
            classification = item.get("classification") or "unknown"
            actionability = result_actionability(item)
            existing_id = _existing_capture_id_for_url(url) if url else None

            action = "skip_unknown"
            target_status = None

            if existing_id:
                if actionability["availability_state"] == "UNAVAILABLE" or classification == "discarded":
                    if _capture_has_property_opportunity_v261(existing_id):
                        action = "skip_existing_opportunity_unavailable"
                    else:
                        action = "skip_existing_unavailable"
                else:
                    action = "skip_duplicate"
            else:
                if actionability["actionable"]:
                    action = "would_create_captured"
                    target_status = "captured"
                elif classification == "reviewable":
                    # A review candidate belongs in the capture inbox.  It
                    # remains non-actionable and must not create an
                    # opportunity; the inbox is the human-review boundary.
                    action = "would_create_in_review"
                    target_status = "in_review"
                elif classification == "discarded":
                    action = "skip_discarded"

            totals[action] = totals.get(action, 0) + 1

            actions.append({
                "source": source,
                "url": url,
                "classification": classification,
                "reason": item.get("reason"),
                "existing_capture_id": existing_id,
                "action": action,
                "target_status": target_status,
                "actionability": actionability,
                "title": item.get("title"),
                "price": item.get("price"),
                "location": item.get("location"),
                "municipality": item.get("municipality"),
                "province": item.get("province"),
            })

    return {
        "actions": actions,
        "totals": totals,
    }


def _field_max_len(Model: Any, field_name: str, default: int = 255) -> int:
    try:
        return Model._meta.get_field(field_name).max_length or default
    except Exception:
        return default


def _truncate_for_field(Model: Any, field_name: str, value: Any, default: str = "") -> str:
    text = str(value or default or "").strip()
    max_len = _field_max_len(Model, field_name, 255)
    return text[:max_len]


def _profile_property_type(profile: Any) -> str:
    raw = getattr(profile, "property_types", None) or getattr(profile, "property_type", None) or []
    if isinstance(raw, str):
        if "land" in raw:
            return "land"
        if "commercial" in raw:
            return "commercial"
        if "house" in raw:
            return "house"
        return "flat"
    if isinstance(raw, (list, tuple)) and raw:
        first = str(raw[0])
        if first in {"house", "flat", "land", "commercial"}:
            return first
    return "flat"


def _profile_operation_type(profile: Any, ctx: dict[str, Any]) -> str:
    raw = getattr(profile, "operation_type", None) or ctx.get("operation") or ""
    raw = str(raw).lower()
    if raw in {"rent", "alquiler"}:
        return "rent"
    return "sale"


def _decimal_or_none(value: Any) -> Any:
    from decimal import Decimal, InvalidOperation

    num = _to_float(value)
    if num is None:
        return None
    try:
        return Decimal(str(num))
    except (InvalidOperation, ValueError):
        return None


def _apply_action_plan_to_db(
    profile: Any,
    ctx: dict[str, Any],
    source_coverage: list[dict[str, Any]],
    action_plan: dict[str, Any], search_run: Any | None = None) -> dict[str, Any]:
    from django.utils import timezone
    from django.db import transaction

    CapturedProperty = apps.get_model("inmuebles", "CapturedProperty")

    try:
        from apps.busquedas.services import _get_or_create_real_source
    except Exception as e:
        raise RuntimeError(f"No se pudo importar _get_or_create_real_source: {type(e).__name__}: {e}")

    now = timezone.now()
    created_ids: list[int] = []
    created_review_ids: list[int] = []
    updated_ids: list[int] = []
    skipped: list[dict[str, Any]] = []

    source_map = {}
    for row in source_coverage:
        for candidate in row.get("candidates", []):
            if candidate.get("url"):
                source_map[candidate["url"]] = row.get("source")

    with transaction.atomic():
        for action in action_plan.get("actions", []):
            url = action.get("url")
            source_name = action.get("source") or source_map.get(url) or "unknown"
            classification = action.get("classification")
            planned_action = action.get("action")
            existing_id = action.get("existing_capture_id")

            if not url:
                skipped.append({"url": url, "reason": "missing_url"})
                continue

            if existing_id:
                if planned_action == "would_mark_existing_discarded":
                    obj = CapturedProperty.objects.select_for_update().get(id=existing_id)
                    obj.status = "discarded"
                    obj.discard_reason = _truncate_for_field(
                        CapturedProperty,
                        "discard_reason",
                        f"V2.6.1: {action.get('reason') or 'discarded_by_quality_gate'}",
                    )
                    obj.last_seen_at = now
                    obj.save(update_fields=["status", "discard_reason", "last_seen_at", "updated_at"])
                    updated_ids.append(obj.id)
                else:
                    skipped.append({"url": url, "reason": "duplicate", "existing_capture_id": existing_id})
                continue

            is_actionable_capture = (
                planned_action == "would_create_captured"
                and bool((action.get("actionability") or {}).get("actionable"))
            )
            is_review_capture = planned_action == "would_create_in_review"
            if not (is_actionable_capture or is_review_capture):
                skipped.append({"url": url, "reason": planned_action})
                continue

            target_status = "in_review" if is_review_capture else "captured"

            source_obj = _get_or_create_real_source(source_name, url)

            title = action.get("title") or f"{source_name} · captación V2.6.1"
            location = action.get("location") or getattr(profile, "province", "") or ""

            obj = CapturedProperty.objects.create(
                search_profile=profile,
                search_run=search_run,
                source=source_obj,
                owner=profile.owner,
                entry_mode="ai_exploration",
                operation_type=_profile_operation_type(profile, ctx),
                source_url=url,
                source_external_id=_truncate_for_field(CapturedProperty, "source_external_id", _external_id_from_url(url)),
                title=_truncate_for_field(CapturedProperty, "title", title, "Captación V2.6.1"),
                description_raw=f"SOOI V2.6.1 · {source_name} · {action.get('reason') or ''}",
                province=_truncate_for_field(CapturedProperty, "province", getattr(profile, "province", "") or ctx.get("province", "")),
                municipality=_truncate_for_field(CapturedProperty, "municipality", action.get("municipality") or ""),
                zone_text=_truncate_for_field(CapturedProperty, "zone_text", getattr(profile, "zone", "") or location),
                property_type=_profile_property_type(profile),
                price=_decimal_or_none(action.get("price")),
                bedrooms=getattr(profile, "min_bedrooms", None),
                status=target_status,
                review_status="pending",
                possible_duplicate=False,
                is_interesting=False,
                ai_summary=f"Captación generada por SOOI V2.6.1 desde {source_name}. Clasificación: {classification}.",
                ai_signals=[
                    {
                        "source": source_name,
                        "classification": classification,
                        "reason": action.get("reason"),
                        "method": "hybrid_coverage_v261",
                    }
                ],
                manual_notes="",
                discard_reason="",
                captured_at=now,
                last_seen_at=now,
            )
            created_ids.append(obj.id)
            if is_review_capture:
                created_review_ids.append(obj.id)

    return {
        "created": len(created_ids),
        "created_in_review": len(created_review_ids),
        "updated": len(updated_ids),
        "skipped": len(skipped),
        "created_ids": created_ids,
        "updated_ids": updated_ids,
        "skipped_items": skipped[:20],
    }



def _profile_property_types_list(profile: Any) -> list[str]:
    raw = getattr(profile, "property_types", None)
    if raw is None:
        raw = getattr(profile, "property_type", None)

    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        text = str(raw or "").strip()
        values = []
        if text:
            try:
                import ast
                parsed = ast.literal_eval(text)
                if isinstance(parsed, (list, tuple, set)):
                    values = list(parsed)
                else:
                    values = [text]
            except Exception:
                values = [text]

    normalized = []
    for value in values:
        v = str(value or "").strip().lower()
        if v:
            normalized.append(v)
    return normalized


def _terrenos_es_applicable(profile: Any, ctx: dict[str, Any]) -> bool:
    """
    terrenos.es no es solo terrenos:
    también puede tener casas, chalets, fincas y rústicas con vivienda.
    """
    types = set(_profile_property_types_list(profile))

    if not types:
        return True

    allowed = {
        "land", "house",
        "terreno", "terrenos",
        "casa", "casas",
        "chalet", "chalets",
        "finca", "fincas",
        "rustica", "rústica",
        "rustico", "rústico",
    }

    return bool(types.intersection(allowed))


def _is_ai_method_source(source_row: dict[str, Any], item: dict[str, Any]) -> bool:
    provider = str(item.get("provider") or "").lower()
    method = str(source_row.get("method") or "").lower()
    return provider == "openai_web_search" or method == "openai_web_search"


def _looks_like_listing_url_v261(source: str, url: str) -> bool:
    u = str(url or "").lower()

    if source == "idealista":
        if "/geo/" in u:
            return True
        if "/alquiler-viviendas/" in u and "/inmueble/" not in u:
            return True
        if "/venta-viviendas/" in u and "/inmueble/" not in u:
            return True
        return "/inmueble/" not in u

    if source == "milanuncios":
        # Milanuncios: las categorías también terminan en .htm.
        # Solo aceptamos anuncio individual si acaba con ID numérico.
        if re.search(r"-\d+\.htm/?$", u):
            return False
        return True

    if source == "yaencontre":
        return "/inmueble-" not in u

    if source == "pisos.com":
        path = urlparse(u).path.lower()
        if ("/alquilar/" in path or "/comprar/" in path) and re.search(r"-\d+_\d+/?$", path):
            return False
        return True

    if source == "terrenos.es":
        path = urlparse(u).path.lower()
        if re.search(r"/(?:urbano|rustico|rústico|solar|terreno|casa|vivienda)/\d+/?$", path):
            return False
        return True

    # Para el resto, de momento solo bloqueamos búsquedas/listados obvios.
    listing_markers = [
        "/buscar/",
        "/search",
    ]
    return any(marker in u for marker in listing_markers)


def _ai_strict_discard_reason_v261(source_row: dict[str, Any], item: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    Return only unusable discovery artifacts and known hard mismatches.

    Missing hard-constrained values fail closed. An inconclusive availability
    probe alone remains reviewable because it does not contradict listing data.
    """
    source = str(source_row.get("source") or "").lower()
    url = str(item.get("url") or "").strip()
    probe = item.get("probe") or {}

    if not url:
        return "ai_missing_url"

    if _looks_like_listing_url_v261(source, url):
        return "ai_listing_url_not_individual_detail"

    # Report exact hard mismatches even when the availability probe is noisy.
    violations = _candidate_constraint_violations(ctx, item)
    if violations:
        return ";".join(violations)

    unknowns = _candidate_constraint_unknowns(ctx, item)
    if unknowns:
        return ";".join(unknowns)

    availability_state, availability_reason = _classify_probe_outcome(probe)
    if availability_state == "MISMATCH":
        if probe.get("unavailable_reason"):
            return f"ai_probe_unavailable:{probe.get('unavailable_reason')}"
        return availability_reason

    return ""


def _postprocess_strict_quality_gate_v261(source_coverage: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
    """
    Reprocesa candidatos después de extracción/probe.
    Endurece IA y recalcula contadores por fuente.
    """
    for source_row in source_coverage:
        candidates = source_row.get("candidates") or []

        for item in candidates:
            if not _is_ai_method_source(source_row, item):
                continue

            reason = _ai_strict_discard_reason_v261(source_row, item, ctx)
            if reason:
                item["classification"] = "discarded"
                item["reason"] = reason
                continue

            # AI candidates remain reviewable. UNKNOWN reasons are retained for auditability.
            availability_state, availability_reason = _classify_probe_outcome(item.get("probe") or {})
            unknowns = _candidate_constraint_unknowns(ctx, item)
            item["classification"] = "reviewable"
            reasons = unknowns + ([availability_reason] if availability_state == "UNKNOWN" else [])
            item["reason"] = ";".join(reasons) if reasons else "ai_candidate_passed_strict_review_gate"

        source_row["candidate_count"] = len(candidates)
        source_row["verified"] = sum(1 for c in candidates if c.get("classification") == "verified")
        source_row["reviewable"] = sum(1 for c in candidates if c.get("classification") == "reviewable")
        source_row["discarded"] = sum(1 for c in candidates if c.get("classification") == "discarded")

        if source_row.get("attempted") and candidates:
            source_row["status"] = "success"
        elif source_row.get("attempted") and not candidates and source_row.get("status") != "failed":
            source_row["status"] = "no_results"

def _plain_place_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _norm_place_text(value: Any) -> str:
    import unicodedata

    text = _plain_place_text(value).lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_province_scope_zone(zone: str, province: str) -> bool:
    z = _norm_place_text(zone)
    p = _norm_place_text(province)

    if not z:
        return True

    province_markers = [
        "provincia",
        "province",
        "provincial",
        "toda la provincia",
        "area provincial",
    ]

    if any(marker in z for marker in province_markers):
        return True

    if p and z in {f"{p} provincia", f"provincia de {p}", f"{p} province"}:
        return True

    return False


def _apply_auto_location(profile: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """
    Regla V2.6.1:
    - zone municipio => location = zone
    - zone provincial => location = province
    - zone vacío => location = province
    """
    province = _plain_place_text(getattr(profile, "province", None) or ctx.get("province"))
    scope = getattr(profile, "geography_scope", "") or ""
    if scope:
        from .geography_runtime import (
            geography_runtime_enabled, resolve_profile_geography, runtime_search_locations,
        )
        locations = runtime_search_locations(profile)
        if geography_runtime_enabled():
            geography = resolve_profile_geography(profile)
            ctx["geography_runtime"] = geography.to_snapshot()
            ctx["intent_resolutions"] = ctx["geography_runtime"]["intent_resolutions"]
            ctx["bounded_geography"] = ctx["geography_runtime"]["coverage_units"]
        scope_map = {"multi_municipality": "multi_location", "named_area": "comarca"}
        ctx["location_scope"] = scope_map.get(scope, scope)
        ctx["location"] = (
            getattr(getattr(profile, "geographic_area", None), "name", "")
            if scope == "named_area" else (locations[0] if len(locations) == 1 else province)
        ) or province
        ctx["search_locations"] = locations
        ctx["search_locations_count"] = len(locations)
        ctx["location_source"] = "profile.canonical_geography"
        ctx["search_locations_source"] = "profile.canonical_geography"
        return ctx
    zone = _plain_place_text(getattr(profile, "zone", None))

    if zone and not _is_province_scope_zone(zone, province):
        ctx["location"] = zone
        ctx["location_scope"] = "municipality"
        ctx["location_source"] = "profile.zone"
        return ctx

    if province:
        ctx["location"] = province
        ctx["location_scope"] = "province"
        ctx["location_source"] = "profile.province"
        return ctx

    ctx["location_scope"] = "unknown"
    ctx["location_source"] = "profile_context"
    return ctx


def _province_search_locations_v261(ctx: dict[str, Any]) -> list[str]:
    """
    V2.6.1.5:
    En búsquedas provinciales no basta con consultar la capital.
    Se genera una lista controlada de municipios para ampliar recall sin relajar quality gate.
    """
    province_norm = _norm_place_text(ctx.get("province") or "")
    location_norm = _norm_place_text(ctx.get("location") or "")

    province_map = {
        "malaga": [
            # Costa / área metropolitana / municipios con mercado real
            "Cártama",
            "Rincón de la Victoria",
            "Alhaurín de la Torre",
            "Alhaurín el Grande",
            "Coín",
            "Mijas",
            "Fuengirola",
            "Benalmádena",
            "Torremolinos",
            "Marbella",
            "Estepona",
            "Vélez-Málaga",
            "Torrox",
            "Nerja",
            "Antequera",
            "Ronda",
            "Málaga",
        ],
        "badajoz": [
            "Don Benito",
            "Villanueva de la Serena",
            "Mérida",
            "Badajoz",
            "Almendralejo",
            "Zafra",
            "Montijo",
            "Olivenza",
        ],
    }

    locations = list(province_map.get(province_norm, []))

    # Si no hay mapa específico, mantenemos comportamiento anterior.
    if not locations:
        base = ctx.get("location") or ctx.get("province")
        return [str(base).strip()] if base else []

    # Si location actual no es marcador provincial y no está en lista, lo ponemos primero.
    current = str(ctx.get("location") or "").strip()
    if current and location_norm not in {province_norm, f"{province_norm} provincia", f"provincia de {province_norm}"}:
        if current not in locations:
            locations.insert(0, current)

    out = []
    seen = set()
    for loc in locations:
        clean = str(loc or "").strip()
        key = _norm_place_text(clean)
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)

    return out


def _apply_search_locations_v261(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("search_locations_source") == "profile.canonical_geography" and ctx.get("location_scope") != "province":
        return ctx
    locations = []

    if ctx.get("location_scope") == "province":
        locations = _province_search_locations_v261(ctx)
        ctx["search_locations_source"] = "province_municipality_expansion_v2615"
    else:
        loc = ctx.get("location") or ctx.get("province")
        locations = [str(loc).strip()] if loc else []
        ctx["search_locations_source"] = "single_location"

    ctx["search_locations"] = locations
    ctx["search_locations_count"] = len(locations)
    return ctx


def _provider_execution_locations(ctx: dict[str, Any]) -> list[str]:
    """Place an exact province-name location first without changing scope."""
    locations = list(ctx.get("search_locations") or [])
    province = _norm_place_text(ctx.get("province") or "")
    if not province:
        return locations
    anchor = next((location for location in locations if _norm_place_text(location) == province), None)
    if anchor is None:
        return locations
    return [anchor] + [location for location in locations if location != anchor]


def _deterministic_candidates_expanded_v2615(
    spec: SourceSpec,
    ctx: dict[str, Any],
    timeout: int,
    max_locations: int = 18,
    provenance: list[dict[str, Any]] | None = None,
    request_trace: list[dict[str, Any]] | None = None,
    stop_after_candidates: int | None = None,
    request_governance: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    V2.6.1.6:
    En scope provincial, evita que el primer municipio monopolice los resultados.
    Reparte candidatos por municipio con round-robin y deja que el quality gate decida.
    """
    locations = _provider_execution_locations(ctx)
    if request_governance is not None:
        state = _provider_governance_state(request_governance, spec.slug)
        state["provider_execution_location_order"] = list(locations)
        province = _norm_place_text(ctx.get("province") or "")
        state["provider_anchor_location"] = next(
            (location for location in locations if _norm_place_text(location) == province), None
        )
    if ctx.get("location_scope") not in {"province", "multi_location", "comarca"} or not locations:
        provider = spec.slug
        if request_governance is not None and not _governance_can_request(request_governance, provider):
            state = _provider_governance_state(request_governance, provider)
            if state["circuit_breaker_open"]:
                state["provider_requests_skipped_circuit_breaker"] += 1
            else:
                state["provider_requests_skipped_cap"] += 1
            return [], "provider_request_governance_skipped"
        if request_governance is not None:
            _provider_governance_state(request_governance, provider)["provider_requests_attempted"] += 1
        trace_start = len(request_trace or [])
        items, error = _deterministic_candidates(
            spec, ctx, timeout=timeout, provenance=provenance, request_trace=request_trace,
        )
        if request_governance is not None:
            telemetry = (request_trace or [])[trace_start:] if request_trace is not None else []
            observed = telemetry[-1] if telemetry else {
                "zero_reason": "HTTP_NON_SUCCESS" if error else ("NONZERO" if items else "AMBIGUOUS"),
            }
            _governance_record(request_governance, provider, str(ctx.get("location") or "unknown"), observed)
            state = _provider_governance_state(request_governance, provider)
            if state.get("provider_anchor_location") == str(ctx.get("location") or ""):
                state["provider_anchor_attempted"] = True
                state["provider_anchor_result_class"] = state["records"][-1].get("zero_class")
        return items, error

    buckets: list[tuple[str, list[dict[str, Any]]]] = []
    errors: list[str] = []

    for loc in locations[:max_locations]:
        if request_governance is not None and not _governance_can_request(request_governance, spec.slug):
            state = _provider_governance_state(request_governance, spec.slug)
            if state["circuit_breaker_open"]:
                state["provider_requests_skipped_circuit_breaker"] += 1
            else:
                state["provider_requests_skipped_cap"] += 1
            if request_trace is not None:
                request_trace.append({
                    "provider": spec.slug, "geography": loc,
                    "zero_reason": "CIRCUIT_BREAKER_OPEN",
                    "zero_class": "TECHNICAL",
                    "skipped_due_circuit_breaker": True,
                })
            continue
        if request_governance is not None:
            _provider_governance_state(request_governance, spec.slug)["provider_requests_attempted"] += 1
        trace_start = len(request_trace or [])
        cctx = dict(ctx)
        cctx["location"] = loc
        cctx["location_scope"] = "municipality_probe_from_province"

        items, err = _deterministic_candidates(
            spec, cctx, timeout=timeout, provenance=provenance,
            request_trace=request_trace,
        )

        if request_governance is not None:
            telemetry = (request_trace or [])[trace_start:] if request_trace is not None else []
            observed = telemetry[-1] if telemetry else {
                "zero_reason": "HTTP_NON_SUCCESS" if err else ("NONZERO" if items else "AMBIGUOUS"),
            }
            _governance_record(request_governance, spec.slug, loc, observed)
            state = _provider_governance_state(request_governance, spec.slug)
            if state.get("provider_anchor_location") == loc:
                state["provider_anchor_attempted"] = True
                state["provider_anchor_result_class"] = state["records"][-1].get("zero_class")

        if err:
            errors.append(f"{loc}:{err}")

        clean_items = []
        for item in items:
            item = dict(item)
            item["search_location"] = loc
            clean_items.append(item)

        clean_items = _dedupe_candidates(clean_items)
        if clean_items:
            buckets.append((loc, clean_items))
        if stop_after_candidates and len(_dedupe_candidates([
            item for _location, bucket in buckets for item in bucket
        ])) >= stop_after_candidates:
            break

    if not buckets:
        return [], "; ".join(errors[:8]) if errors else None

    interleaved: list[dict[str, Any]] = []
    max_len = max(len(items) for _, items in buckets)

    for idx in range(max_len):
        for _loc, items in buckets:
            if idx < len(items):
                interleaved.append(items[idx])

    deduped = _dedupe_candidates(interleaved)
    if deduped:
        return deduped, None

    return [], "; ".join(errors[:8]) if errors else None


def _ai_candidates_expanded(
    spec: SourceSpec, ctx: dict[str, Any], max_results: int, max_locations: int = 4,
    circuit: ProviderCircuit | None = None, before_call=None, after_call=None,
    stage_trace: dict[str, Any] | None = None, batch_trace: list[dict[str, Any]] | None = None,
    provenance: list[dict[str, Any]] | None = None,
    consolidate_locations: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Cover every municipality in at most ``max_locations`` AI calls per source."""
    circuit = circuit or ProviderCircuit("openai")

    def governed_call(prompt: str) -> ProviderResult[str]:
        if circuit.state is ProviderCircuitState.OPEN:
            return ProviderResult(
                ProviderOutcome.SKIPPED_CIRCUIT,
                reason=circuit.reason or "provider_circuit_open",
            )
        # Tuple support keeps older focused tests and local call-site mocks valid.
        if before_call is not None and not before_call():
            return ProviderResult(ProviderOutcome.SKIPPED_CIRCUIT, reason="planner_limit")
        token = _OPENAI_CIRCUIT_CONTEXT.set(circuit)
        try:
            before_provenance = len(provenance) if provenance is not None else 0
            if provenance is None:
                # Preserve the historical call shape for focused mocks and
                # non-coverage callers that did not request provenance.
                result = _call_openai_web_search(prompt)
            else:
                result = _call_openai_web_search(
                    prompt, provenance=provenance, source_provider=spec.slug,
                )
            if provenance is not None:
                for item in provenance[before_provenance:]:
                    item["planned"] = {
                        **item.get("planned", {}),
                        "provider": spec.slug,
                        "operation": _canonical_operation(ctx.get("operation")),
                        "geography": str(ctx.get("location") or ctx.get("province") or "unknown"),
                    }
        finally:
            _OPENAI_CIRCUIT_CONTEXT.reset(token)
        if isinstance(result, tuple):
            text, error = result
            result = ProviderResult(
                ProviderOutcome.RETRYABLE_FAILURE if error else ProviderOutcome.SUCCESS,
                value=text,
                reason=error,
            )
        if result.outcome is ProviderOutcome.HARD_FAILURE and circuit.state.value != "OPEN":
            circuit.open(result.reason or "provider_hard_failure")
        if after_call is not None:
            after_call(result.outcome is ProviderOutcome.SUCCESS)
        return result

    locations = list(ctx.get("search_locations") or [])
    if ctx.get("location_scope") not in {"multi_location", "comarca"} or len(locations) < 2:
        prompt = _openai_prompt(spec, ctx, max_results=max_results)
        result = governed_call(prompt)
        before_format = int((stage_trace or {}).get("format_noncompliance_count", 0))
        items = _items_from_ai_text(result.value or "", spec, stage_trace)[:max_results]
        if batch_trace is not None:
            batch_trace.append({
                "locations": locations or [ctx.get("location")], "outcome": result.outcome.value,
                "format_conclusive": int((stage_trace or {}).get("format_noncompliance_count", 0)) == before_format,
            })
        return items, result.reason

    merged, errors = [], []
    call_count = 1 if consolidate_locations else min(len(locations), max_locations)
    from .adaptive_planner import location_batches
    batches = location_batches(locations, call_count)

    per_batch = max(1, (max_results + call_count - 1) // call_count)
    for batch in batches:
        child = dict(ctx)
        child.update(
            location=batch[0],
            location_scope="municipality_probe_batch",
            search_locations=batch,
        )
        result = governed_call(
            _openai_prompt(spec, child, max_results=per_batch)
        )
        before_format = int((stage_trace or {}).get("format_noncompliance_count", 0))
        items = _items_from_ai_text(result.value or "", spec, stage_trace)
        if batch_trace is not None:
            batch_trace.append({
                "locations": list(batch), "outcome": result.outcome.value,
                "format_conclusive": int((stage_trace or {}).get("format_noncompliance_count", 0)) == before_format,
            })
        if result.reason:
            errors.append(f"{', '.join(batch)}:{result.reason}")
        for item in items:
            item.setdefault("search_location", batch[0])
            item.setdefault("search_location_batch", list(batch))
            merged.append(item)
        if result.outcome in (ProviderOutcome.HARD_FAILURE, ProviderOutcome.SKIPPED_CIRCUIT):
            break
    return _dedupe_candidates(merged)[:max_results], "; ".join(errors[:4]) or None


def _is_pre_provider_gate_reason(reason: Any) -> bool:
    """Recognize planner decisions that happen before a provider invocation."""
    normalized = str(reason or "").lower().replace("-", "_")
    return any(marker in normalized for marker in (
        "planner_limit", "budget_exhaust", "budget_gate", "pre_provider",
        "controlled_omission", "mode_limit", "plan_limit",
    ))


def _refresh_safe_stage_counters(row: dict[str, Any]) -> None:
    """Project aggregate diagnostics only; candidate/provider bodies are excluded."""
    counters = row.setdefault("stage_counters", {})
    candidates = row.get("candidates") or []
    counters["normalized_or_deduped_candidate_count"] = int(row.get("candidate_count") or 0)
    counters["accepted_candidate_count"] = sum(
        1 for item in candidates
        if item.get("classification") in {"verified", "reviewable"}
    )
    reasons = [str(item.get("reason") or "") for item in candidates if item.get("classification") == "discarded"]
    counters["rejected_geography_count"] = sum("location_outside_search_scope" in reason for reason in reasons)
    counters["rejected_price_count"] = sum(
        any(marker in reason for marker in ("price_above_max", "price_below_min", "price_implausible"))
        for reason in reasons
    )
    counters["rejected_bedroom_count"] = sum("bedrooms_below_min" in reason for reason in reasons)
    counters["rejected_property_type_count"] = sum("property_type_mismatch" in reason for reason in reasons)
    counters["rejected_missing_evidence_count"] = sum(
        any(marker in reason for marker in ("ai_missing_url", "unknown_required_attribute:"))
        for reason in reasons
    )
    counters["format_noncompliance_count"] = int(counters.get("format_noncompliance_count") or 0)
    if not counters.get("provider_response_present"):
        counters["zero_stage"] = "provider_response_zero"
    elif int(counters.get("extracted_candidate_count") or 0) == 0:
        counters["zero_stage"] = (
            "format_noncompliance" if counters["format_noncompliance_count"]
            else "extraction_zero"
        )
    elif counters["normalized_or_deduped_candidate_count"] == 0:
        counters["zero_stage"] = "normalization_zero"
    elif counters["accepted_candidate_count"] == 0:
        counters["zero_stage"] = "hard_filter_rejection"
    else:
        counters["zero_stage"] = "accepted"


def _refresh_provider_efficiency(row: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Derive source metrics from final semantics; never influence decisions."""
    from .search_quality_semantics import result_actionability

    candidates = row.get("candidates") or []
    semantics = [
        result_actionability(
            candidate,
            hard_violations=_candidate_constraint_violations(ctx, candidate),
        )
        for candidate in candidates
    ]
    candidate_count = len(candidates)
    hard_clean = sum(bool(item["hard_clean"]) for item in semantics)
    actionable = sum(bool(item["actionable"]) for item in semantics)
    review_required = sum(bool(item["review_required"]) for item in semantics)
    availability_states = [str(item.get("availability_state") or "").lower() for item in semantics]
    availability_confirmed = sum(state == "confirmed" for state in availability_states)
    availability_unknown = sum(state == "unknown" for state in availability_states)
    availability_unavailable = sum(state == "unavailable" for state in availability_states)
    review_availability = sum(
        state == "unknown" and bool(item["hard_clean"])
        for state, item in zip(availability_states, semantics)
    )
    row["provider_efficiency"] = {
        "candidate_count": candidate_count,
        "hard_clean_count": hard_clean,
        "actionable_count": actionable,
        "review_required_count": review_required,
        "hard_rejected_count": candidate_count - hard_clean,
        "availability_confirmed_count": availability_confirmed,
        "availability_unknown_count": availability_unknown,
        "availability_unavailable_count": availability_unavailable,
        "review_availability_count": review_availability,
        "provider_yield_pct": round(hard_clean * 100 / candidate_count, 2) if candidate_count else 0,
        "commercial_yield_pct": round(actionable * 100 / candidate_count, 2) if candidate_count else 0,
    }


def _aggregate_safe_stage_counters(source_coverage: list[dict[str, Any]]) -> dict[str, Any]:
    # These counters describe the AI provider candidate flow only. Mixing in
    # deterministic/reused rows would make provider and extraction stages
    # incomparable and could produce normalized > extracted telemetry.
    rows = [
        row.get("stage_counters") or {} for row in source_coverage
        if row.get("attempted") and row.get("method") == "openai_web_search"
    ]
    inferable = bool(rows) and all(
        row.get("provider_raw_item_count") != "UNKNOWN" for row in rows
    )
    aggregate = {
        "provider_response_present": any(bool(row.get("provider_response_present")) for row in rows),
        "provider_raw_item_count": (
            sum(int(row.get("provider_raw_item_count") or 0) for row in rows) if inferable else "UNKNOWN"
        ),
        **{
            key: sum(int(row.get(key) or 0) for row in rows)
            for key in (
                "extracted_candidate_count", "normalized_or_deduped_candidate_count",
                "accepted_candidate_count", "rejected_geography_count", "rejected_price_count",
                "rejected_bedroom_count", "rejected_property_type_count",
                "rejected_missing_evidence_count",
                "format_noncompliance_count",
            )
        },
    }
    if not aggregate["provider_response_present"]:
        aggregate["zero_stage"] = "provider_response_zero"
    elif aggregate["extracted_candidate_count"] == 0:
        aggregate["zero_stage"] = (
            "format_noncompliance" if aggregate["format_noncompliance_count"]
            else "extraction_zero"
        )
    elif aggregate["normalized_or_deduped_candidate_count"] == 0:
        aggregate["zero_stage"] = "normalization_zero"
    elif aggregate["accepted_candidate_count"] == 0:
        aggregate["zero_stage"] = "hard_filter_rejection"
    else:
        aggregate["zero_stage"] = "accepted"
    return aggregate


def _postprocess_low_price_review_v2617(source_coverage: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
    """
    V2.6.1.7:
    En alquiler residencial, un precio demasiado bajo suele ser error de portal,
    habitación, garaje, temporada parcial o parseo incompleto.
    No lo descartamos automáticamente, pero lo bajamos a revisión humana.
    """
    operation = str(ctx.get("operation") or "").lower()
    property_types = set(ctx.get("property_types") or [])

    if "rent" not in operation and "alquiler" not in operation:
        return

    if not property_types.intersection({"house", "flat"}):
        return

    # Umbral conservador para España. No bloquea oportunidades; evita captured automático.
    min_reasonable_rent = 250.0

    for row in source_coverage:
        for item in row.get("candidates") or []:
            if item.get("classification") not in ("verified", "reviewable"):
                continue

            price = _to_float(item.get("price"))
            if price is None:
                continue

            if price < min_reasonable_rent:
                item["classification"] = "reviewable"
                item["reason"] = f"low_price_requires_review:{price:g}<{min_reasonable_rent:g}"


# SR0_SEARCH_RECOVERY_V1
# Recuperación comercial del buscador:
# - listas explícitas de ubicaciones no son un único municipio;
# - Los Pedroches se trata como ámbito comarcal;
# - 403 anti-bot de fuentes conocidas es inconcluso, no anuncio inexistente;
# - Fotocasa HTTP 200 + marcador genérico "no está disponible" pasa a revisión
#   si respeta los filtros, en vez de descartarse automáticamente.

_SR0_LOS_PEDROCHES = [
    "Pozoblanco",
    "Alcaracejos",
    "Añora",
    "Belalcázar",
    "Cardeña",
    "Conquista",
    "Dos Torres",
    "El Guijo",
    "El Viso",
    "Fuente La Lancha",
    "Hinojosa del Duque",
    "Pedroche",
    "Santa Eufemia",
    "Torrecampo",
    "Villanueva de Córdoba",
    "Villanueva del Duque",
    "Villaralto",
]


def _sr0_split_explicit_locations(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw or "," not in raw:
        return []
    seen = set()
    result = []
    for part in raw.split(","):
        item = re.sub(r"\s+", " ", part).strip(" ,;")
        key = _ascii_slug(item)
        if item and key and key not in seen:
            seen.add(key)
            result.append(item)
    return result if len(result) > 1 else []


def _sr0_repair_search_locations(ctx: dict[str, Any]) -> None:
    if ctx.get("search_locations_source") == "profile.canonical_geography":
        return
    raw_location = str(ctx.get("location") or "").strip()
    norm = _ascii_slug(raw_location, sep=" ")

    if norm in {"los pedroches", "valle de los pedroches"}:
        ctx["location_scope"] = "comarca"
        ctx["search_locations"] = list(_SR0_LOS_PEDROCHES)
        ctx["search_locations_count"] = len(_SR0_LOS_PEDROCHES)
        ctx["search_locations_source"] = "sr0_los_pedroches_comarca_v1"
        return

    explicit = _sr0_split_explicit_locations(raw_location)
    if explicit:
        ctx["location_scope"] = "multi_location"
        ctx["search_locations"] = explicit
        ctx["search_locations_count"] = len(explicit)
        ctx["search_locations_source"] = "sr0_explicit_multi_location_v1"


def _sr0_is_known_antibot_403(source: str, probe: dict[str, Any]) -> bool:
    source = str(source or "").lower()
    if source not in {"idealista", "yaencontre", "terrenos.es"}:
        return False
    status = probe.get("http_status")
    error = str(probe.get("error") or "")
    return str(status) == "403" or "403" in error


def _sr0_postprocess_fotocasa_inconclusive(
    source_coverage: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> None:
    # Fotocasa puede devolver HTTP 200 y contener el texto genérico
    # "no está disponible" en HTML. SR0 no lo eleva a verified:
    # queda reviewable si respeta los filtros.
    for row in source_coverage:
        if str(row.get("source") or "").lower() != "fotocasa":
            continue

        for item in row.get("candidates") or []:
            if item.get("classification") != "discarded":
                continue

            probe = item.get("probe") or {}
            reason = str(item.get("reason") or probe.get("unavailable_reason") or "").lower()

            if str(probe.get("http_status")) != "200":
                continue
            if "no está disponible" not in reason and "no esta disponible" not in reason:
                continue

            violations = _candidate_constraint_violations(ctx, item)
            if violations:
                item["classification"] = "discarded"
                item["reason"] = ";".join(violations)
            else:
                item["classification"] = "reviewable"
                item["reason"] = "sr0_fotocasa_http200_availability_inconclusive"

        candidates = row.get("candidates") or []
        row["verified"] = sum(1 for c in candidates if c.get("classification") == "verified")
        row["reviewable"] = sum(1 for c in candidates if c.get("classification") == "reviewable")
        row["discarded"] = sum(1 for c in candidates if c.get("classification") == "discarded")


_MAX_NEAR_MATCHES_TOTAL = 12
_NEAR_MATCH_PROVIDERS_V1 = frozenset({"fotocasa", "habitaclia"})
_DETERMINISTIC_REQUEST_CAP_PER_PROVIDER = 6
_DETERMINISTIC_TECHNICAL_FAILURE_THRESHOLD = 2


def _new_deterministic_request_governance() -> dict[str, Any]:
    return {
        "provider_request_cap": _DETERMINISTIC_REQUEST_CAP_PER_PROVIDER,
        "technical_failure_threshold": _DETERMINISTIC_TECHNICAL_FAILURE_THRESHOLD,
        "providers": {},
    }


def _provider_governance_state(governance: dict[str, Any], provider: str) -> dict[str, Any]:
    return governance.setdefault("providers", {}).setdefault(provider, {
        "provider_requests_attempted": 0,
        "provider_requests_succeeded": 0,
        "provider_requests_technical_failed": 0,
        "provider_requests_business_zero": 0,
        "provider_requests_ambiguous": 0,
        "provider_requests_skipped_circuit_breaker": 0,
        "provider_requests_skipped_cap": 0,
        "consecutive_technical_failures": 0,
        "circuit_breaker_open": False,
        "circuit_breaker_reason": "",
        "circuit_breaker_eligible_failure_count": 0,
        "provider_execution_location_order": [],
        "provider_anchor_location": None,
        "provider_anchor_attempted": False,
        "provider_anchor_result_class": None,
        "records": [],
    })


def _governance_zero_class(telemetry: dict[str, Any]) -> str:
    existing = str(telemetry.get("zero_class") or "").upper()
    if existing in {"NONZERO", "BUSINESS", "AMBIGUOUS", "TECHNICAL"}:
        return existing
    reason = str(telemetry.get("zero_reason") or "").upper()
    if reason == "NONZERO":
        return "NONZERO"
    if reason == "PROVIDER_EMPTY_CONFIRMED":
        return "BUSINESS"
    if reason == "SCOPE_FOUND_NO_ANCHORS":
        return "AMBIGUOUS"
    if reason in {"RESULT_SCOPE_NOT_FOUND", "HTTP_NON_SUCCESS", "UNEXPECTED_REDIRECT"}:
        return "TECHNICAL"
    return "TECHNICAL" if reason else "AMBIGUOUS"


def _governance_can_request(governance: dict[str, Any], provider: str) -> bool:
    state = _provider_governance_state(governance, provider)
    if state["circuit_breaker_open"]:
        return False
    return state["provider_requests_attempted"] < int(
        governance.get("provider_request_cap") or _DETERMINISTIC_REQUEST_CAP_PER_PROVIDER
    )


def _governance_record(
    governance: dict[str, Any], provider: str, geography: str, telemetry: dict[str, Any],
) -> None:
    state = _provider_governance_state(governance, provider)
    zero_class = _governance_zero_class(telemetry)
    state["records"].append({"geography": geography, **telemetry, "zero_class": zero_class})
    if zero_class == "NONZERO":
        state["provider_requests_succeeded"] += 1
        state["consecutive_technical_failures"] = 0
    elif zero_class == "BUSINESS":
        state["provider_requests_succeeded"] += 1
        state["provider_requests_business_zero"] += 1
        state["consecutive_technical_failures"] = 0
    elif zero_class == "AMBIGUOUS":
        state["provider_requests_ambiguous"] += 1
        state["consecutive_technical_failures"] = 0
    else:
        state["provider_requests_technical_failed"] += 1
        state["circuit_breaker_eligible_failure_count"] += 1
        state["consecutive_technical_failures"] += 1
        if state["consecutive_technical_failures"] >= int(
            governance.get("technical_failure_threshold") or _DETERMINISTIC_TECHNICAL_FAILURE_THRESHOLD
        ):
            state["circuit_breaker_open"] = True
            state["circuit_breaker_reason"] = "consecutive_technical_failures"


def _governance_eligible_locations(governance: dict[str, Any], provider: str) -> list[str]:
    state = _provider_governance_state(governance, provider)
    return [
        str(record["geography"])
        for record in state.get("records", [])
        if record.get("zero_class") == "BUSINESS"
    ]


def _recovery_constraints(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_price": str(ctx.get("min_price")) if ctx.get("min_price") not in (None, "") else None,
        "max_price": str(ctx.get("max_price")) if ctx.get("max_price") not in (None, "") else None,
        "min_bedrooms": ctx.get("bedrooms"),
        "property_types": list(ctx.get("property_types") or []),
        "locations": list(ctx.get("search_locations") or []),
    }


def _deterministic_exact_candidate_count(
    source_coverage: list[dict[str, Any]], ctx: dict[str, Any],
) -> int:
    return sum(
        1 for row in source_coverage
        if row.get("method") == "deterministic"
        for candidate in (row.get("candidates") or [])
        if not _candidate_constraint_violations(ctx, candidate)
        and candidate.get("classification") in {"verified", "reviewable"}
    )


def _near_match_allowed(
    tier: str, original_ctx: dict[str, Any], effective_ctx: dict[str, Any], candidate: dict[str, Any],
) -> bool:
    """Allow exactly the single advertised relaxation; fail closed otherwise."""
    original = _candidate_constraint_violations(original_ctx, candidate)
    if _candidate_constraint_unknowns(original_ctx, candidate):
        return False
    if _candidate_constraint_violations(effective_ctx, candidate):
        return False
    availability_state = str((candidate.get("availability_evidence") or {}).get("state") or "").lower()
    if availability_state not in {"confirmed", "unknown"}:
        return False
    if tier == "BUDGET_PLUS_10_PERCENT":
        return len(original) == 1 and original[0].startswith("price_above_max:")
    if tier == "BEDROOM_MINUS_ONE":
        return len(original) == 1 and original[0].startswith("bedrooms_below_min:")
    return False


def _run_zero_result_recovery(
    specs: list[SourceSpec], source_coverage: list[dict[str, Any]], ctx: dict[str, Any],
    timeout: int, max_results_per_source: int, detail_probe_cache: dict[str, dict[str, Any]],
    request_governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run two bounded deterministic-only tiers and keep alternatives out of exact buckets."""
    specs = [spec for spec in specs if spec.slug in _NEAR_MATCH_PROVIDERS_V1]
    result: dict[str, Any] = {
        "deterministic_exact_zero": True,
        "fallback_executed": bool(specs),
        "fallback_stop_reason": "no_near_matches" if specs else "unsupported_deterministic_provider",
        "near_match_tier_used": None,
        "near_match_candidates": [],
        "exact_candidates_recovered": [],
        "fallback_trace": [],
        "fallback_skipped_technical_zero_count": 0,
    }
    if not specs:
        return result
    if request_governance is not None and not any(
        _governance_eligible_locations(request_governance, spec.slug) for spec in specs
    ):
        result["fallback_executed"] = False
        result["fallback_stop_reason"] = "technical_zero_not_fallback_eligible"
        return result
    row_by_source = {row.get("source"): row for row in source_coverage}
    original_constraints = _recovery_constraints(ctx)
    tiers: list[tuple[str, dict[str, Any]]] = []
    if ctx.get("max_price") not in (None, ""):
        budget_ctx = dict(ctx)
        budget_ctx["max_price"] = Decimal(str(ctx["max_price"])) * Decimal("1.10")
        tiers.append(("BUDGET_PLUS_10_PERCENT", budget_ctx))
    bedrooms = int(ctx.get("bedrooms") or 0)
    if bedrooms > 1:
        bedroom_ctx = dict(ctx)
        bedroom_ctx["bedrooms"] = max(1, bedrooms - 1)
        bedroom_ctx["min_bedrooms"] = bedroom_ctx["bedrooms"]
        tiers.append(("BEDROOM_MINUS_ONE", bedroom_ctx))

    seen = set()
    for tier, effective_ctx in tiers:
        tier_near: list[dict[str, Any]] = []
        tier_exact: list[dict[str, Any]] = []
        tier_trace = {
            "tier": tier,
            "providers_attempted": [],
            "effective_constraints": _recovery_constraints(effective_ctx),
            "raw_candidates": 0,
            "parsed_candidates": 0,
            "accepted_near_matches": 0,
            "rejected_reason_counts": {},
        }
        for spec in specs:
            eligible_locations = (
                _governance_eligible_locations(request_governance, spec.slug)
                if request_governance is not None else list(effective_ctx.get("search_locations") or [])
            )
            if request_governance is not None and not eligible_locations:
                result["fallback_skipped_technical_zero_count"] += 1
                counts = tier_trace["rejected_reason_counts"]
                counts["technical_zero_not_fallback_eligible"] = int(
                    counts.get("technical_zero_not_fallback_eligible") or 0
                ) + 1
                continue
            remaining = _MAX_NEAR_MATCHES_TOTAL - len(tier_near)
            if remaining <= 0:
                break
            row = row_by_source.get(spec.slug)
            provenance = row.get("query_provenance") if row else None
            request_trace = row.get("deterministic_request_telemetry") if row else None
            provider_ctx = dict(effective_ctx)
            if request_governance is not None:
                provider_ctx["search_locations"] = eligible_locations
                provider_ctx["search_locations_count"] = len(eligible_locations)
            items, _error = _deterministic_candidates_expanded_v2615(
                spec, provider_ctx, timeout=timeout,
                provenance=provenance, request_trace=request_trace,
                stop_after_candidates=remaining,
                request_governance=request_governance,
            )
            tier_trace["providers_attempted"].append(spec.slug)
            tier_trace["raw_candidates"] += len(items)
            tier_trace["parsed_candidates"] += len(items)
            verdicts = _classify_targeted_detail_candidates(
                spec, items[:max_results_per_source], effective_ctx, timeout,
                probe_cache=detail_probe_cache,
                cap=min(max_results_per_source, remaining),
            )
            for verdict in verdicts:
                key = _normalize_url(verdict.get("url") or "")
                if not key or key in seen:
                    continue
                original_violations = _candidate_constraint_violations(ctx, verdict)
                if not original_violations and not _candidate_constraint_unknowns(ctx, verdict) \
                        and verdict.get("classification") == "verified":
                    exact = dict(verdict)
                    exact["exact_match"] = True
                    exact["discovered_during_fallback"] = True
                    exact["source"] = spec.slug
                    tier_exact.append(exact)
                    seen.add(key)
                    continue
                if verdict.get("classification") not in {"verified", "reviewable"} or not _near_match_allowed(
                    tier, ctx, effective_ctx, verdict,
                ):
                    reason = str(verdict.get("reason") or "rejected")
                    counts = tier_trace["rejected_reason_counts"]
                    counts[reason] = int(counts.get(reason) or 0) + 1
                    continue
                near = dict(verdict)
                near.update({
                    "provider": spec.slug,
                    "classification": "near_match",
                    "exact_match": False,
                    "match_tier": tier,
                    "relaxation_reason": original_violations[0],
                    "original_constraints": original_constraints,
                    "effective_constraints": _recovery_constraints(effective_ctx),
                    "alternative_label": (
                        "Supera el presupuesto máximo hasta un 10 %"
                        if tier == "BUDGET_PLUS_10_PERCENT"
                        else "Tiene un dormitorio menos que el mínimo solicitado"
                    ),
                })
                if str((near.get("availability_evidence") or {}).get("state") or "").lower() == "unknown":
                    near["availability_review"] = True
                    near["alternative_label"] += " · Disponibilidad por revisar"
                tier_near.append(near)
                tier_trace["accepted_near_matches"] += 1
                seen.add(key)
                if len(tier_near) >= _MAX_NEAR_MATCHES_TOTAL:
                    break

        result["fallback_trace"].append(tier_trace)
        for exact in tier_exact:
            target = row_by_source.get(exact.get("source"))
            if target is not None:
                existing = {_normalize_url(item.get("url") or "") for item in target.get("candidates") or []}
                if _normalize_url(exact.get("url") or "") not in existing:
                    target.setdefault("candidates", []).append(exact)
                    target["candidate_count"] = len(target["candidates"])
                    target["status"] = "success"
            result["exact_candidates_recovered"].append(exact)
        if tier_exact:
            result["fallback_stop_reason"] = "exact_matches_recovered"
            break
        if tier_near:
            result["near_match_candidates"] = tier_near[:_MAX_NEAR_MATCHES_TOTAL]
            result["near_match_tier_used"] = tier
            result["fallback_stop_reason"] = "near_match_cap_reached" if len(tier_near) >= _MAX_NEAR_MATCHES_TOTAL else "near_matches_found"
            break
    return result


def run_hybrid_discovery_v261(
    profile_id: int,
    write: bool = False,
    timeout: int = 12,
    max_results_per_source: int = 10,
    use_ai: bool = True,
    location_override: str | None = None,
    search_run: Any | None = None,) -> dict[str, Any]:
    # write=True está permitido solo desde el comando con --confirm-write.

    SearchProfile = apps.get_model("busquedas", "SearchProfile")
    profile = SearchProfile.objects.get(id=profile_id)
    ctx = _profile_context(profile)
    ctx["property_types"] = _profile_property_types_list(profile)
    ctx["terrenos_es_applicable"] = _terrenos_es_applicable(profile, ctx)
    _apply_auto_location(profile, ctx)
    if location_override:
        ctx["location"] = str(location_override).strip()
        ctx["location_scope"] = "override"
        ctx["location_source"] = "command.location_override"

    _apply_search_locations_v261(ctx)
    _sr0_repair_search_locations(ctx)

    source_coverage = []
    detail_probe_cache: dict[str, dict[str, Any]] = {}
    deterministic_request_governance = _new_deterministic_request_governance()
    coverage_batch_trace: list[dict[str, Any]] = []
    reuse_observability: dict[str, Any] = {
        "reuse_applied": False,
        "reused_from_run_id": None,
        "reused_candidate_count": 0,
        "reused_source_count": 0,
        "fresh_provider_requests_attempted": 0,
        "fresh_external_calls_attempted": 0,
        "provider_request_governance_live_exercised": False,
        "provider_request_governance_live_state": "NOT_EXERCISED_DUE_TO_REUSE",
    }
    recovery_result: dict[str, Any] = {
        "deterministic_exact_zero": False,
        "fallback_executed": False,
        "fallback_stop_reason": "exact_matches_present",
        "near_match_tier_used": None,
        "near_match_candidates": [],
        "exact_candidates_recovered": [],
    }
    # One circuit per hybrid execution: shared by every municipality batch and
    # every source using the same external provider.
    openai_circuit = ProviderCircuit("openai")

    # Flag-off and ungoverned runs deliberately keep the historical topology.
    planner = None
    execution_specs = SOURCE_SPECS
    if search_run is not None:
        from .adaptive_planner import planner_for_run
        planner = planner_for_run(search_run, SOURCE_SPECS)
        if planner:
            execution_specs = planner.allowed

            # LOCAL is real persisted evidence. It is evaluated before any
            # deterministic fetch or provider boundary and keeps its original
            # classifications/provenance intact.
            from .search_reuse import reusable_evidence
            reused_run, reused_rows = reusable_evidence(search_run)
            source_coverage.extend(reused_rows)
            if reused_rows:
                reuse_observability.update({
                    "reuse_applied": True,
                    "reused_from_run_id": reused_run.pk if reused_run else None,
                    "reused_candidate_count": sum(
                        len(row.get("candidates") or []) for row in reused_rows
                    ),
                    "reused_source_count": len({row.get("source") for row in reused_rows if row.get("source")}),
                })
                for row in reused_rows:
                    row["telemetry_origin_run_id"] = reused_run.pk if reused_run else None
                    provenance = row.get("query_provenance")
                    if isinstance(provenance, dict):
                        provenance["telemetry_origin_run_id"] = reused_run.pk if reused_run else None
                        response = provenance.get("response")
                        if isinstance(response, dict):
                            response["telemetry_origin_run_id"] = reused_run.pk if reused_run else None
                from .adaptive_planner import actionable_sufficient_coverage
                planner.observe_deterministic_coverage(reused_rows)
                if actionable_sufficient_coverage(planner.mode, source_coverage, planner.policy):
                    planner.stop("sufficient_coverage", execution_specs)
                    execution_specs = []

    for spec_index, spec in enumerate(execution_specs):
        if not _is_applicable(spec, ctx):
            row = _coverage_row(spec, False, "not_applicable", "not_applicable")
            source_coverage.append(row)
            continue

        row = _coverage_row(spec, True, spec.method, "failed")
        raw_candidates: list[dict[str, Any]] = []
        error = None
        source_result_cap = _external_result_cap(spec, max_results_per_source)
        row.setdefault("stage_counters", {})["requested_result_cap"] = source_result_cap
        if spec.slug == "idealista":
            row["stage_counters"]["query_group_count"] = len(_idealista_query_groups(ctx))
            row["stage_counters"]["direct_listing_url_rejected"] = 0

        if spec.method == "deterministic":
            raw_candidates, error = _deterministic_candidates_expanded_v2615(
                spec, ctx, timeout=timeout, provenance=row["query_provenance"],
                request_trace=row["deterministic_request_telemetry"],
                request_governance=deterministic_request_governance,
            )
        else:
            if use_ai:
                if openai_circuit.state.value == "OPEN":
                    row["attempted"] = False
                    row["status"] = "skipped_circuit"
                    row["provider_outcome"] = ProviderOutcome.SKIPPED_CIRCUIT.value
                    row["provider_reason"] = openai_circuit.reason
                    row["error"] = "provider_circuit_open"
                    source_coverage.append(row)
                    continue
                stage_trace: dict[str, Any] = {}
                raw_candidates, error = _ai_candidates_expanded(
                    spec, ctx, max_results=source_result_cap,
                    circuit=openai_circuit,
                    before_call=planner.before_external_call if planner else None,
                    after_call=planner.after_external_call if planner else None,
                    stage_trace=stage_trace,
                    batch_trace=coverage_batch_trace if planner else None,
                    provenance=row["query_provenance"],
                    consolidate_locations=bool(
                        planner and planner.mode == "eco" and planner.budget_max == Decimal("1")
                    ),
                )
                row["stage_counters"].update({
                    "provider_response_present": bool(stage_trace.get("provider_response_present")),
                    "provider_raw_item_count": (
                        int(stage_trace.get("provider_raw_item_count", 0))
                        if stage_trace.get("provider_raw_item_count_inferable") else "UNKNOWN"
                    ),
                    "extracted_candidate_count": int(stage_trace.get("extracted_candidate_count", 0)),
                    "format_noncompliance_count": int(stage_trace.get("format_noncompliance_count", 0)),
                    "missing_title_count": int(stage_trace.get("missing_title_count", 0)),
                    "direct_listing_url_rejected": int(stage_trace.get("direct_listing_url_rejected", 0)),
                    "ai_items_returned": int(stage_trace.get("provider_raw_item_count", 0))
                    if stage_trace.get("provider_raw_item_count_inferable") else "UNKNOWN",
                })
                if openai_circuit.state.value == "OPEN":
                    row["provider_outcome"] = ProviderOutcome.HARD_FAILURE.value
                    row["provider_reason"] = openai_circuit.reason
                elif error and _is_pre_provider_gate_reason(error):
                    row["attempted"] = False
                    row["provider_outcome"] = ProviderOutcome.SKIPPED_CIRCUIT.value
                    row["provider_reason"] = error
                elif error:
                    row["provider_outcome"] = ProviderOutcome.RETRYABLE_FAILURE.value
                    row["provider_reason"] = error
                else:
                    row["provider_outcome"] = ProviderOutcome.SUCCESS.value
            else:
                error = "ai_disabled_by_option"

        pre_dedupe_count = len(raw_candidates)
        raw_candidates = _dedupe_candidates(raw_candidates)
        row["stage_counters"]["duplicates_removed"] = max(0, pre_dedupe_count - len(raw_candidates))
        row["stage_counters"]["parsed_candidates"] = len(raw_candidates)
        if spec.slug == "idealista":
            row["stage_counters"]["direct_listing_url_accepted"] = len(raw_candidates)
            row["stage_counters"]["direct_listing_url_rejected"] = int(
                row["stage_counters"].get("direct_listing_url_rejected") or 0
            )
            row["stage_counters"]["stable_id_count"] = sum(
                1 for candidate in raw_candidates
                if _external_id_from_url(candidate.get("source_url") or "")
            )
            try:
                ai_items_returned = int(row["stage_counters"].get("ai_items_returned") or 0)
            except (TypeError, ValueError):
                ai_items_returned = 0
            row["stage_counters"]["output_truncated"] = bool(
                ai_items_returned >= source_result_cap
                or len(raw_candidates) >= source_result_cap
            )
        raw_candidates = raw_candidates[:source_result_cap]
        row["candidate_count"] = len(raw_candidates)
        row["stage_counters"]["normalized_or_deduped_candidate_count"] = len(raw_candidates)

        if raw_candidates:
            if spec.method == "deterministic" and spec.slug in {"fotocasa", "habitaclia"}:
                # Sequential access makes the run-local URL cache an exact
                # at-most-once network boundary. The existing per-source result
                # cap bounds this targeted verification stage.
                verdicts = _classify_targeted_detail_candidates(
                    spec, raw_candidates, ctx, timeout,
                    probe_cache=detail_probe_cache, cap=max_results_per_source,
                )
            else:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    verdicts = list(pool.map(
                        lambda item: _classify_candidate(spec, item, ctx=ctx, timeout=timeout),
                        raw_candidates,
                    ))
            row["candidates"] = verdicts
            if spec.method == "deterministic" and spec.slug in {"fotocasa", "habitaclia"}:
                row.update(_detail_verification_counters(verdicts))
            row["verified"] = sum(1 for v in verdicts if v["classification"] == "verified")
            row["reviewable"] = sum(1 for v in verdicts if v["classification"] == "reviewable")
            row["discarded"] = sum(1 for v in verdicts if v["classification"] == "discarded")
            if spec.slug == "idealista":
                row["stage_counters"].update({
                    "hard_rejected_count": row["discarded"],
                    "review_count": row["reviewable"],
                    "actionable_count": row["verified"],
                    "final_candidate_count": len(verdicts),
                })
            row["status"] = "success"
            row["error"] = error
        else:
            row["status"] = (
                "omitted" if _is_pre_provider_gate_reason(error) else
                "failed" if error and error != "ai_disabled_by_option" else
                "no_results"
            )
            row["error"] = error

        source_coverage.append(row)
        if planner:
            planner.record_executed(spec)
            # Coverage is based on final quality classifications, not raw or
            # duplicate discovery rows. Deterministic sources form one stage;
            # AI sources are evaluated individually.
            _postprocess_strict_quality_gate_v261(source_coverage, ctx)
            _sr0_postprocess_fotocasa_inconclusive(source_coverage, ctx)
            _postprocess_low_price_review_v2617(source_coverage, ctx)
            from .adaptive_planner import actionable_sufficient_coverage, source_tier, SourceTier
            tier = source_tier(spec)
            deterministic_stage_done = tier != SourceTier.DETERMINISTIC or not any(
                source_tier(item) == SourceTier.DETERMINISTIC
                for item in execution_specs[spec_index + 1:]
            )
            if tier == SourceTier.DETERMINISTIC and deterministic_stage_done:
                for observed_row in source_coverage:
                    if observed_row.get("method") == "deterministic":
                        _refresh_provider_efficiency(observed_row, ctx)
                if _deterministic_exact_candidate_count(source_coverage, ctx) == 0:
                    recovery_result = _run_zero_result_recovery(
                        [item for item in execution_specs if item.method == "deterministic"],
                        source_coverage, ctx, timeout, max_results_per_source,
                        detail_probe_cache, request_governance=deterministic_request_governance,
                    )
                    for observed_row in source_coverage:
                        if observed_row.get("method") == "deterministic":
                            candidates = observed_row.get("candidates") or []
                            observed_row["verified"] = sum(c.get("classification") == "verified" for c in candidates)
                            observed_row["reviewable"] = sum(c.get("classification") == "reviewable" for c in candidates)
                            observed_row["discarded"] = sum(c.get("classification") == "discarded" for c in candidates)
                            _refresh_provider_efficiency(observed_row, ctx)
                planner.observe_deterministic_coverage(source_coverage)
            if deterministic_stage_done and actionable_sufficient_coverage(
                planner.mode, source_coverage, planner.policy,
            ):
                planner.stop("sufficient_coverage", execution_specs[spec_index + 1:])
                break
            if planner.stop_reason == "budget_exhausted":
                planner.stop("budget_exhausted", execution_specs[spec_index + 1:])
                break
            if openai_circuit.state.value == "OPEN":
                planner.provider_degraded = True
                planner.stop("provider_hard_failure", execution_specs[spec_index + 1:])
                break

    if planner:
        # Keep every source visible and explain why it was not part of executed
        # coverage. This includes mode limits and early adaptive stops.
        existing = {row["source"] for row in source_coverage}
        for spec in planner.specs:
            if spec.slug not in existing:
                row = _coverage_row(spec, False, "planner_omitted", "omitted")
                row["error"] = planner.stop_reason or "mode_limit"
                source_coverage.append(row)
        if planner.stop_reason is None and not any(
            row.get("attempted") for row in source_coverage
        ):
            planner.stop_reason = "no_applicable_sources"
        elif planner.stop_reason is None:
            from .search_quality_semantics import evaluate_search_quality_semantics
            provider_failures = evaluate_search_quality_semantics({
                "context": ctx, "source_coverage": source_coverage,
            })["actual_provider_failure_count"]
            if provider_failures:
                planner.provider_degraded = True
                planner.stop_reason = "provider_outage"
        planner.apply_to_run(search_run, source_coverage)

    applicable = [r for r in source_coverage if r["status"] != "not_applicable"]
    is_complete = all(r["attempted"] for r in applicable)

    _postprocess_strict_quality_gate_v261(source_coverage, ctx)
    _sr0_postprocess_fotocasa_inconclusive(source_coverage, ctx)
    _postprocess_low_price_review_v2617(source_coverage, ctx)
    for row in source_coverage:
        candidates = row.get("candidates") or []
        row["verified"] = sum(1 for v in candidates if v.get("classification") == "verified")
        row["reviewable"] = sum(1 for v in candidates if v.get("classification") == "reviewable")
        row["discarded"] = sum(1 for v in candidates if v.get("classification") == "discarded")
        _refresh_safe_stage_counters(row)
        _refresh_provider_efficiency(row, ctx)
    coverage_contract = None
    if planner:
        from .adaptive_planner import coverage_plan, classify_coverage_result
        from .models import SearchRun
        coverage_contract = coverage_plan(
            ctx.get("search_locations") or ([ctx.get("location")] if ctx.get("location") else []),
            search_run.budget_max_credits,
            maximum_calls=min(4, planner.policy.max_ai_calls),
            planned_calls=planner.calls_planned,
            consolidate_locations=bool(
                planner.mode == "eco" and planner.budget_max == Decimal("1")
            ),
        )
        if planner.mode == "eco" and planner.budget_max == Decimal("1"):
            coverage_contract.update({
                "eco_consolidated_locations_count": len(ctx.get("search_locations") or []),
                "eco_consolidated_call_count": 1 if ctx.get("search_locations") else 0,
            })
        if ctx.get("geography_runtime"):
            coverage_contract["geography_runtime"] = ctx["geography_runtime"]
        format_degraded = any(
            int((row.get("stage_counters") or {}).get("format_noncompliance_count") or 0)
            for row in source_coverage
        )
        completed_locations = []
        conclusive_calls = 0
        for trace in coverage_batch_trace:
            if trace.get("outcome") == ProviderOutcome.SUCCESS.value and trace.get("format_conclusive", True):
                conclusive_calls += 1
                completed_locations.extend(trace.get("locations") or [])
        planned_locations = coverage_contract["planned_units"]
        executed = min(planned_locations, len(list(filter(None, completed_locations))))
        from .search_quality_semantics import evaluate_search_quality_semantics
        quality_semantics = evaluate_search_quality_semantics({
            "context": ctx, "source_coverage": source_coverage,
        })
        accepted = quality_semantics["actionable_count"]
        degraded = (
            format_degraded or planner.provider_degraded
            or quality_semantics["actual_provider_failure_count"] > 0
        )
        classification = classify_coverage_result(
            coverage_contract, executed_units=executed, accepted_candidates=accepted,
            degraded=degraded,
            stop_reason="format_noncompliance" if format_degraded else planner.stop_reason,
        )
        full = classification.pop("is_full_plan")
        coverage_contract.update({
            "calls_attempted": search_run.calls_attempted,
            "calls_succeeded": conclusive_calls,
            "calls_provider_succeeded": search_run.calls_succeeded,
            "credits_consumed": float(search_run.budget_consumed_credits or 0),
            "accepted_candidates": accepted,
            "stop_reason": (
                "completed_plan" if full else
                "format_noncompliance" if format_degraded else
                planner.stop_reason or "plan_limit"
            ),
            "format_degraded": format_degraded,
            "reused": bool(search_run.reused_from_search_run_id),
        })
        coverage_contract.update(classification)
        locations = list(ctx.get("search_locations") or [])
        source_planned = len(planner.specs)
        fresh_sources = {
            row.get("source") for row in source_coverage
            if row.get("attempted") and not row.get("reused") and row.get("source")
        }
        reused_sources = {
            row.get("source") for row in source_coverage
            if row.get("reused") and row.get("source")
        }
        effectively_covered_sources = fresh_sources | reused_sources
        source_executed = len(fresh_sources)
        external_planned = int(planner.calls_planned)
        external_executed = sum(
            1 for trace in coverage_batch_trace
            if trace.get("outcome") == ProviderOutcome.SUCCESS.value
        )
        geographic_executed = len(dict.fromkeys(filter(None, completed_locations)))
        coverage_contract.update({
            "geographic_locations_planned": len(locations),
            "geographic_locations_executed": geographic_executed,
            "geographic_coverage_percent": round(100 * geographic_executed / len(locations), 2) if locations else None,
            "sources_planned": source_planned,
            "sources_executed": source_executed,
            "sources_freshly_executed": source_executed,
            "sources_reused": len(reused_sources),
            "sources_effectively_covered": len(effectively_covered_sources),
            "sources_omitted": max(0, source_planned - len(effectively_covered_sources)),
            "source_coverage_percent": round(100 * len(effectively_covered_sources) / source_planned, 2) if source_planned else None,
            "external_calls_planned": external_planned,
            "external_calls_executed": external_executed,
            "external_call_coverage_percent": round(100 * external_executed / external_planned, 2) if external_planned else None,
        })
        search_run.sources_executed = source_executed
        search_run.sources_omitted = max(0, source_planned - len(effectively_covered_sources))
        if not full and not degraded:
            coverage_contract["coverage_status"] = search_run.coverage_status
        if coverage_contract.get("coverage_axes"):
            coverage_contract["coverage_axes"].update({
                "geographic_locations_executed": len(dict.fromkeys(filter(None, completed_locations))),
                "source_location_units_executed": executed,
            })
        if degraded:
            search_run.coverage_status = SearchRun.CoverageStatus.DEGRADED_PROVIDER
        elif full:
            search_run.coverage_status = SearchRun.CoverageStatus.FULL
            search_run.stop_reason = SearchRun.StopReason.COMPLETED_PLAN
    action_plan = _build_action_plan(source_coverage)
    write_result = None
    if write:
        write_result = _apply_action_plan_to_db(profile, ctx, source_coverage, action_plan, search_run=search_run)

    from .search_quality_semantics import evaluate_search_quality_semantics
    quality_semantics = evaluate_search_quality_semantics({
        "context": ctx, "source_coverage": source_coverage,
    })
    if coverage_contract is not None:
        coverage_contract["quality_semantics"] = quality_semantics

    near_matches = recovery_result.get("near_match_candidates") or []
    exact_count = sum(
        1 for row in source_coverage for candidate in (row.get("candidates") or [])
        if candidate.get("classification") in {"verified", "reviewable"}
        and not _candidate_constraint_violations(ctx, candidate)
    )
    recovery_observability = {
        "exact_candidate_count": exact_count,
        "near_match_candidate_count": len(near_matches),
        "near_match_budget_count": sum(item.get("match_tier") == "BUDGET_PLUS_10_PERCENT" for item in near_matches),
        "near_match_bedroom_count": sum(item.get("match_tier") == "BEDROOM_MINUS_ONE" for item in near_matches),
        "near_match_tier_used": recovery_result.get("near_match_tier_used"),
        "deterministic_exact_zero": bool(recovery_result.get("deterministic_exact_zero")),
        "fallback_executed": bool(recovery_result.get("fallback_executed")),
        "fallback_stop_reason": recovery_result.get("fallback_stop_reason"),
        "fallback_trace": recovery_result.get("fallback_trace") or [],
        "fallback_skipped_technical_zero_count": int(
            recovery_result.get("fallback_skipped_technical_zero_count") or 0
        ),
    }
    governance_observability = {
        "provider_request_cap": deterministic_request_governance["provider_request_cap"],
        "providers": deterministic_request_governance.get("providers", {}),
        "provider_requests_attempted": sum(
            int(state.get("provider_requests_attempted") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_succeeded": sum(
            int(state.get("provider_requests_succeeded") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_technical_failed": sum(
            int(state.get("provider_requests_technical_failed") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_business_zero": sum(
            int(state.get("provider_requests_business_zero") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_ambiguous": sum(
            int(state.get("provider_requests_ambiguous") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_skipped_circuit_breaker": sum(
            int(state.get("provider_requests_skipped_circuit_breaker") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "provider_requests_skipped_cap": sum(
            int(state.get("provider_requests_skipped_cap") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "circuit_breaker_eligible_failure_count": sum(
            int(state.get("circuit_breaker_eligible_failure_count") or 0)
            for state in deterministic_request_governance.get("providers", {}).values()
        ),
        "fallback_eligible_geographies": {
            provider: _governance_eligible_locations(deterministic_request_governance, provider)
            for provider in deterministic_request_governance.get("providers", {})
        },
        "provider_execution_location_order": {
            provider: state.get("provider_execution_location_order") or []
            for provider, state in deterministic_request_governance.get("providers", {}).items()
        },
        "provider_anchor_location": {
            provider: state.get("provider_anchor_location")
            for provider, state in deterministic_request_governance.get("providers", {}).items()
        },
        "provider_anchor_attempted": {
            provider: bool(state.get("provider_anchor_attempted"))
            for provider, state in deterministic_request_governance.get("providers", {}).items()
        },
        "provider_anchor_result_class": {
            provider: state.get("provider_anchor_result_class")
            for provider, state in deterministic_request_governance.get("providers", {}).items()
        },
    }
    fresh_deterministic_requests = governance_observability["provider_requests_attempted"]
    fresh_external_calls = max(
        0,
        int(planner.calls_attempted if planner else 0)
        - int(planner.start_calls_attempted if planner else 0),
    )
    reuse_observability.update({
        "fresh_provider_requests_attempted": fresh_deterministic_requests,
        "fresh_external_calls_attempted": fresh_external_calls,
        "provider_request_governance_live_exercised": bool(fresh_deterministic_requests),
        "provider_request_governance_live_state": (
            "EXERCISED" if fresh_deterministic_requests else
            "NOT_EXERCISED_DUE_TO_REUSE" if reuse_observability["reuse_applied"]
            else "NOT_EXERCISED"
        ),
    })
    if coverage_contract is not None:
        coverage_contract.update(recovery_observability)
        coverage_contract["provider_request_governance"] = governance_observability
        coverage_contract["reuse_observability"] = reuse_observability

    return {
        "version": "V2.6.1.8-write" if write else "V2.6.1.8-dry-run",
        "profile_id": profile_id,
        "write": bool(write),
        "context": ctx,
        "is_complete": is_complete,
        "source_coverage": source_coverage,
        "planner_efficiency": planner.efficiency_observability() if planner else None,
        "provider_request_governance": governance_observability,
        "reuse_observability": reuse_observability,
        "stage_counters": _aggregate_safe_stage_counters(source_coverage),
        "action_plan": action_plan,
        "write_result": write_result,
        "reused": bool(search_run and search_run.reused_from_search_run_id),
        "reused_from_search_run_id": (
            search_run.reused_from_search_run_id if search_run else None
        ),
        "coverage_contract": coverage_contract,
        "quality_semantics": quality_semantics,
        "zero_result_recovery": recovery_observability,
        "near_match_candidates": near_matches,
        "totals": {
            "sources": len(source_coverage),
            "applicable": len(applicable),
            "attempted": sum(1 for r in source_coverage if r["attempted"]),
            "success": sum(1 for r in source_coverage if r["status"] == "success"),
            "no_results": sum(1 for r in source_coverage if r["status"] == "no_results"),
            "failed": sum(1 for r in source_coverage if r["status"] == "failed"),
            "not_applicable": sum(1 for r in source_coverage if r["status"] == "not_applicable"),
            "skipped_circuit": sum(1 for r in source_coverage if r["status"] == "skipped_circuit"),
            "verified": sum(r["verified"] for r in source_coverage),
            "reviewable": sum(r["reviewable"] for r in source_coverage),
            "hard_clean": quality_semantics["hard_clean_count"],
            "review_required": quality_semantics["review_required_count"],
            "actionable": quality_semantics["actionable_count"],
            "unavailable": quality_semantics["unavailable_count"],
            "discarded": sum(r["discarded"] for r in source_coverage),
            "would_create_captured": action_plan["totals"].get("would_create_captured", 0),
            "would_create_in_review": action_plan["totals"].get("would_create_in_review", 0),
            "hold_for_review": action_plan["totals"].get("hold_for_review", 0),
            "skip_discarded": action_plan["totals"].get("skip_discarded", 0),
            "skip_duplicate": action_plan["totals"].get("skip_duplicate", 0),
            "would_mark_existing_discarded": action_plan["totals"].get("would_mark_existing_discarded", 0),
        },
    }
