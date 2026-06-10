from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.apps import apps


@dataclass(frozen=True)
class SourceSpec:
    slug: str
    domains: tuple[str, ...]
    method: str
    property_scope: str = "any"


SOURCE_SPECS: list[SourceSpec] = [
    SourceSpec("idealista", ("idealista.com",), "openai_web_search"),
    SourceSpec("fotocasa", ("fotocasa.es",), "deterministic"),
    SourceSpec("habitaclia", ("habitaclia.com",), "deterministic"),
    SourceSpec("pisos.com", ("pisos.com",), "openai_web_search"),
    SourceSpec("yaencontre", ("yaencontre.com",), "openai_web_search"),
    SourceSpec("milanuncios", ("milanuncios.com",), "openai_web_search"),
    SourceSpec("servihabitat", ("servihabitat.com",), "openai_web_search"),
    SourceSpec("solvia", ("solvia.es",), "openai_web_search"),
    SourceSpec("altamira", ("altamirainmuebles.com",), "openai_web_search"),
    SourceSpec("terrenos.es", ("terrenos.es",), "openai_web_search", property_scope="all"),
]


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
    min_area = _get_field(profile, ["min_area", "area_min", "m2_min", "min_m2"], None)

    return {
        "location": _clean_text(location),
        "province": _clean_text(province),
        "operation": _clean_text(operation).lower(),
        "is_land": is_land,
        "max_price": max_price,
        "min_price": min_price,
        "bedrooms": bedrooms,
        "min_area": min_area,
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


def _loose_json(text: str) -> Any:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
    raw = re.sub(r"```$", "", raw).strip()

    for candidate in [raw]:
        try:
            return json.loads(candidate)
        except Exception:
            pass

    match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


def _items_from_ai_text(text: str, spec: SourceSpec) -> list[dict[str, Any]]:
    data = _loose_json(text)
    if isinstance(data, dict):
        for key in ["results", "items", "properties", "candidates", "anuncios"]:
            if isinstance(data.get(key), list):
                data = data[key]
                break

    items: list[dict[str, Any]] = []

    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            url = _normalize_url(row.get("source_url") or row.get("url") or row.get("link") or "")
            if not url or not _domain_matches(url, spec.domains):
                continue
            items.append({
                "source": spec.slug,
                "source_url": url,
                "title": _clean_text(row.get("title") or row.get("titulo") or ""),
                "price": row.get("price") or row.get("precio"),
                "location": _clean_text(row.get("location") or row.get("ubicacion") or ""),
                "summary": _clean_text(row.get("summary") or row.get("descripcion") or row.get("description") or ""),
                "raw": row,
                "provider": "openai_web_search",
            })

    if not items:
        for url in _extract_urls_from_text(text, spec.domains):
            items.append({
                "source": spec.slug,
                "source_url": url,
                "title": "",
                "price": None,
                "location": "",
                "summary": "",
                "raw": {},
                "provider": "openai_web_search_regex",
            })

    return _dedupe_candidates(items)


def _dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for item in items:
        url = _normalize_url(item.get("source_url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        item["source_url"] = url
        out.append(item)
    return out


def _probe_url(url: str, timeout: int = 12) -> dict[str, Any]:
    result = {
        "http_status": None,
        "final_url": url,
        "html_len": 0,
        "available": False,
        "unavailable_reason": None,
        "error": None,
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
            text = body.decode("utf-8", errors="ignore").lower()
            result["http_status"] = getattr(resp, "status", None)
            result["final_url"] = getattr(resp, "url", url)
            result["html_len"] = len(body)

            for pattern in UNAVAILABLE_PATTERNS:
                if pattern in text:
                    result["unavailable_reason"] = pattern
                    result["available"] = False
                    return result

            result["available"] = bool(result["http_status"] and 200 <= int(result["http_status"]) < 400 and len(body) > 500)

    except HTTPError as e:
        result["http_status"] = e.code
        try:
            body = e.read(80000)
            text = body.decode("utf-8", errors="ignore").lower()
            result["html_len"] = len(body)
            for pattern in UNAVAILABLE_PATTERNS:
                if pattern in text:
                    result["unavailable_reason"] = pattern
                    break
        except Exception:
            pass
        result["error"] = f"HTTPError: {e.code}"

    except URLError as e:
        result["error"] = f"URLError: {e.reason}"

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

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
        if ctx.get("max_price"):
            params.append(f"pmax={ctx['max_price']}")
        qs = ("?" + "&".join(params)) if params else ""
        return f"https://www.habitaclia.com/{operation}-{_ascii_slug(loc, '_')}.htm{qs}"

    if spec.slug == "fotocasa":
        return f"https://www.fotocasa.es/es/{operation}/viviendas/{_ascii_slug(loc)}/todas-las-zonas/l"

    return ""


def _deterministic_candidates(spec: SourceSpec, ctx: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    url = _deterministic_url(spec, ctx)
    if not url:
        return [], "no_location_for_deterministic_url"

    try:
        from apps.busquedas.services_portal_extractors import probe_portal_url
    except Exception as e:
        return [], f"extractor_import_error: {type(e).__name__}: {e}"

    try:
        res = probe_portal_url(spec.slug, url, municipality=ctx.get("location") or "", timeout=timeout)
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
                "location": _clean_text(row.get("location") or row.get("ubicacion") or ctx.get("location") or ""),
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

    filter_text = ", ".join(filters) if filters else "sin filtros adicionales claros"
    source_rules = _source_url_rules_v2616(spec)

    search_locations = ctx.get("search_locations") or []
    if search_locations and len(search_locations) > 1:
        location_instruction = (
            f"{operation} vivienda/inmueble en la provincia de {ctx.get('province') or location}. "
            f"No limites la búsqueda a la capital. Busca también en estos municipios: "
            f"{', '.join(search_locations)}."
        )
    else:
        location_instruction = f"{operation} vivienda/inmueble en {location}."

    return f"""
Busca anuncios inmobiliarios reales y actuales exclusivamente en {spec.slug}.

{domain_header}
{domains}

Consulta:
{location_instruction} Filtros: {filter_text}.

Reglas:
- {domain_rule}
- No inventes URLs.
- Prioriza fichas individuales reales de inmueble frente a listados.\n- {source_rules}
- No devuelvas páginas de búsqueda genéricas si puedes devolver fichas individuales.
- En búsquedas provinciales, reparte resultados entre municipios si existen candidatos fiables.
- Respeta estrictamente precio máximo, dormitorios mínimos y operación.
- Si no encuentras resultados fiables, devuelve [].
- Máximo {max_results} resultados.
- Formato estricto JSON, sin explicación:

[
  {{
    "title": "string",
    "source_url": "https://...",
    "price": "string or null",
    "location": "string",
    "summary": "string"
  }}
]
""".strip()


def _call_openai_web_search(prompt: str) -> tuple[str, str | None]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "", "OPENAI_API_KEY_not_configured"

    try:
        from openai import OpenAI
    except Exception as e:
        return "", f"openai_import_error: {type(e).__name__}: {e}"

    model = (
        os.environ.get("SOOI_OPENAI_WEB_SEARCH_MODEL")
        or os.environ.get("OPENAI_WEB_SEARCH_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4.1-mini"
    )

    client = OpenAI(api_key=api_key)

    last_error = None
    for tool_type in ["web_search", "web_search_preview"]:
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                tools=[{"type": tool_type}],
            )
            text = getattr(response, "output_text", "") or ""
            if not text:
                try:
                    text = response.model_dump_json()
                except Exception:
                    text = str(response)
            return text, None
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"

    return "", last_error or "openai_web_search_failed"



def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        pass

    raw = str(value).strip()
    clean = re.sub(r"[^\d,\.]", "", raw)
    if not clean:
        return None

    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    elif clean.count(".") > 1:
        clean = clean.replace(".", "")

    try:
        return float(clean)
    except Exception:
        return None


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

    return violations

def _classify_candidate(spec: SourceSpec, item: dict[str, Any], ctx: dict[str, Any], timeout: int) -> dict[str, Any]:
    url = item["source_url"]
    probe = _probe_url(url, timeout=timeout)

    verdict = {
        "url": url,
        "title": item.get("title") or "",
        "price": item.get("price"),
        "location": item.get("location") or "",
        "provider": item.get("provider"),
        "classification": "reviewable",
        "reason": "default_reviewable",
        "probe": probe,
    }

    if probe.get("http_status") == 404 or probe.get("unavailable_reason"):
        verdict["classification"] = "discarded"
        verdict["reason"] = probe.get("unavailable_reason") or "http_404_or_unavailable"
        return verdict

    violations = _candidate_constraint_violations(ctx, item)
    if violations:
        verdict["classification"] = "discarded"
        verdict["reason"] = ";".join(violations)
        return verdict

    if spec.slug == "idealista":
        verdict["classification"] = "reviewable"
        verdict["reason"] = "idealista_never_auto_captured_without_strong_validation"
        return verdict

    if item.get("provider") == "deterministic" and probe.get("available"):
        verdict["classification"] = "verified"
        verdict["reason"] = "deterministic_candidate_probe_available"
        return verdict

    if probe.get("available"):
        verdict["classification"] = "reviewable"
        verdict["reason"] = "ai_candidate_probe_available"
        return verdict

    verdict["classification"] = "reviewable"
    verdict["reason"] = "not_strong_enough_to_verify_but_not_discarded"
    return verdict


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
    actions: list[dict[str, Any]] = []
    totals: dict[str, int] = {}

    for source_row in source_coverage:
        source = source_row.get("source")
        for item in source_row.get("candidates") or []:
            url = item.get("url") or ""
            classification = item.get("classification") or "unknown"
            existing_id = _existing_capture_id_for_url(url) if url else None

            action = "skip_unknown"
            target_status = None

            if existing_id:
                if classification == "discarded":
                    if _capture_has_property_opportunity_v261(existing_id):
                        action = "skip_existing_opportunity_unavailable"
                    else:
                        action = "skip_existing_unavailable"
                else:
                    action = "skip_duplicate"
            else:
                if classification == "verified":
                    action = "would_create_captured"
                    target_status = "captured"
                elif classification == "reviewable":
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
                "title": item.get("title"),
                "price": item.get("price"),
                "location": item.get("location"),
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

            if planned_action not in {"would_create_captured", "would_create_in_review"}:
                skipped.append({"url": url, "reason": planned_action})
                continue

            target_status = "captured" if classification == "verified" else "in_review"

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
                municipality=_truncate_for_field(CapturedProperty, "municipality", location),
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

    return {
        "created": len(created_ids),
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
        if "/alquilar/" in u and re.search(r"-\d+_\d+/?$", u):
            return False
        return True

    # Para el resto, de momento solo bloqueamos búsquedas/listados obvios.
    listing_markers = [
        "/buscar/",
        "/search",
        "?",
    ]
    return any(marker in u for marker in listing_markers)


def _ai_strict_discard_reason_v261(source_row: dict[str, Any], item: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    Quality gate estricto para candidatos generados por IA/Web Search.
    Un candidato IA solo puede quedar reviewable si:
    - es ficha individual, no listado,
    - tiene probe disponible,
    - no devuelve 403/404/error,
    - tiene precio,
    - no viola precio/zona.
    """
    source = str(source_row.get("source") or "").lower()
    url = str(item.get("url") or "").strip()
    probe = item.get("probe") or {}

    if not url:
        return "ai_missing_url"

    if _looks_like_listing_url_v261(source, url):
        return "ai_listing_url_not_individual_detail"

    status = probe.get("http_status")
    error = probe.get("error")
    available = probe.get("available")

    # Idealista bloquea probes HTTP con 403 por anti-bot — no indica que el inmueble
    # no exista. Mismo tratamiento que services.py:600: ignorar el 403 de idealista.
    is_idealista_403 = "idealista" in source and error is not None and "403" in str(error)

    if error and not is_idealista_403:
        return f"ai_probe_error:{error}"

    try:
        if status is not None and int(status) >= 400:
            return f"ai_probe_http_{status}"
    except Exception:
        pass

    if available is False and not is_idealista_403:
        unavailable_reason = probe.get("unavailable_reason") or "not_available"
        return f"ai_probe_unavailable:{unavailable_reason}"

    if available is None and not is_idealista_403:
        return "ai_probe_missing_or_inconclusive"

    if item.get("price") in (None, "", "null"):
        return "ai_missing_price"

    violations = _candidate_constraint_violations(ctx, item)
    if violations:
        return ";".join(violations)

    if ctx.get("location_scope") == "municipality":
        wanted = _norm_place_text(ctx.get("location"))
        haystack = _norm_place_text(
            " ".join([
                str(item.get("title") or ""),
                str(item.get("location") or ""),
                str(item.get("url") or ""),
            ])
        )
        if wanted and wanted not in haystack:
            return f"ai_location_mismatch:{ctx.get('location')}"

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

            # Si pasa todo, IA queda como reviewable, nunca verified.
            item["classification"] = "reviewable"
            item["reason"] = "ai_candidate_passed_strict_review_gate"

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


def _deterministic_candidates_expanded_v2615(
    spec: SourceSpec,
    ctx: dict[str, Any],
    timeout: int,
    max_locations: int = 18,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    V2.6.1.6:
    En scope provincial, evita que el primer municipio monopolice los resultados.
    Reparte candidatos por municipio con round-robin y deja que el quality gate decida.
    """
    locations = list(ctx.get("search_locations") or [])
    if ctx.get("location_scope") != "province" or not locations:
        return _deterministic_candidates(spec, ctx, timeout=timeout)

    buckets: list[tuple[str, list[dict[str, Any]]]] = []
    errors: list[str] = []

    for loc in locations[:max_locations]:
        cctx = dict(ctx)
        cctx["location"] = loc
        cctx["location_scope"] = "municipality_probe_from_province"

        items, err = _deterministic_candidates(spec, cctx, timeout=timeout)

        if err:
            errors.append(f"{loc}:{err}")

        clean_items = []
        for item in items:
            item = dict(item)
            item["search_location"] = loc
            if not item.get("location"):
                item["location"] = loc
            clean_items.append(item)

        clean_items = _dedupe_candidates(clean_items)
        if clean_items:
            buckets.append((loc, clean_items))

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

    source_coverage = []

    for spec in SOURCE_SPECS:
        if not _is_applicable(spec, ctx):
            row = _coverage_row(spec, False, "not_applicable", "not_applicable")
            source_coverage.append(row)
            continue

        row = _coverage_row(spec, True, spec.method, "failed")
        raw_candidates: list[dict[str, Any]] = []
        error = None

        if spec.method == "deterministic":
            raw_candidates, error = _deterministic_candidates_expanded_v2615(spec, ctx, timeout=timeout)
        else:
            if use_ai:
                prompt = _openai_prompt(spec, ctx, max_results=max_results_per_source)
                text, error = _call_openai_web_search(prompt)
                raw_candidates = _items_from_ai_text(text, spec)[:max_results_per_source]
            else:
                error = "ai_disabled_by_option"

        raw_candidates = _dedupe_candidates(raw_candidates)[:max_results_per_source]
        row["candidate_count"] = len(raw_candidates)

        if raw_candidates:
            verdicts = [_classify_candidate(spec, item, ctx=ctx, timeout=timeout) for item in raw_candidates]
            row["candidates"] = verdicts
            row["verified"] = sum(1 for v in verdicts if v["classification"] == "verified")
            row["reviewable"] = sum(1 for v in verdicts if v["classification"] == "reviewable")
            row["discarded"] = sum(1 for v in verdicts if v["classification"] == "discarded")
            row["status"] = "success"
            row["error"] = error
        else:
            row["status"] = "failed" if error and error != "ai_disabled_by_option" else "no_results"
            row["error"] = error

        source_coverage.append(row)

    applicable = [r for r in source_coverage if r["status"] != "not_applicable"]
    is_complete = all(r["attempted"] for r in applicable)

    _postprocess_strict_quality_gate_v261(source_coverage, ctx)
    _postprocess_low_price_review_v2617(source_coverage, ctx)
    for row in source_coverage:
        candidates = row.get("candidates") or []
        row["verified"] = sum(1 for v in candidates if v.get("classification") == "verified")
        row["reviewable"] = sum(1 for v in candidates if v.get("classification") == "reviewable")
        row["discarded"] = sum(1 for v in candidates if v.get("classification") == "discarded")
    action_plan = _build_action_plan(source_coverage)
    write_result = None
    if write:
        write_result = _apply_action_plan_to_db(profile, ctx, source_coverage, action_plan, search_run=search_run)

    return {
        "version": "V2.6.1.8-write" if write else "V2.6.1.8-dry-run",
        "profile_id": profile_id,
        "write": bool(write),
        "context": ctx,
        "is_complete": is_complete,
        "source_coverage": source_coverage,
        "action_plan": action_plan,
        "write_result": write_result,
        "totals": {
            "sources": len(source_coverage),
            "applicable": len(applicable),
            "attempted": sum(1 for r in source_coverage if r["attempted"]),
            "success": sum(1 for r in source_coverage if r["status"] == "success"),
            "no_results": sum(1 for r in source_coverage if r["status"] == "no_results"),
            "failed": sum(1 for r in source_coverage if r["status"] == "failed"),
            "not_applicable": sum(1 for r in source_coverage if r["status"] == "not_applicable"),
            "verified": sum(r["verified"] for r in source_coverage),
            "reviewable": sum(r["reviewable"] for r in source_coverage),
            "discarded": sum(r["discarded"] for r in source_coverage),
            "would_create_captured": action_plan["totals"].get("would_create_captured", 0),
            "would_create_in_review": action_plan["totals"].get("would_create_in_review", 0),
            "skip_discarded": action_plan["totals"].get("skip_discarded", 0),
            "skip_duplicate": action_plan["totals"].get("skip_duplicate", 0),
            "would_mark_existing_discarded": action_plan["totals"].get("would_mark_existing_discarded", 0),
        },
    }
