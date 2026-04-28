from decimal import Decimal
from django.utils import timezone
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from .ai_discovery import AIDiscoveryClient
from .models import SearchProfile, SearchRun
from urllib.parse import urlparse, urlunparse
from django.utils.text import slugify
import re
import unicodedata
from difflib import SequenceMatcher

def _normalize_property_url(source_url: str) -> str:
    if not source_url:
        return ""

    parsed = urlparse(source_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""

    netloc = parsed.netloc.replace("www.", "").strip().lower()
    path = (parsed.path or "").rstrip("/")

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            "",   # params
            "",   # query
            "",   # fragment
        )
    )

def _normalize_title_for_duplicate_check(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _has_probable_duplicate(
    *,
    owner,
    source,
    operation_type: str,
    property_type: str,
    municipality: str,
    price,
    title: str,
    external_id: str,
) -> bool:
    if not owner or not source or not municipality or price is None or not title:
        return False

    normalized_title = _normalize_title_for_duplicate_check(title)
    if not normalized_title:
        return False

    candidates = (
        CapturedProperty.objects.filter(
            owner=owner,
            source=source,
            operation_type=operation_type,
            property_type=property_type,
            municipality=municipality,
            price=price,
        )
        .exclude(source_external_id=external_id)
        .only("id", "title")
    )

    for candidate in candidates:
        candidate_title = _normalize_title_for_duplicate_check(candidate.title)
        similarity = SequenceMatcher(None, normalized_title, candidate_title).ratio()
        if similarity >= 0.88:
            return True

    return False

def _extract_base_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"

def _extract_hostname(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.netloc.replace("www.", "").strip().lower()


def _source_name_from_hostname(hostname: str) -> str:
    mapping = {
        "idealista.com": "Idealista",
        "www.idealista.com": "Idealista",
        "pisos.com": "pisos.com",
        "www.pisos.com": "pisos.com",
        "habitaclia.com": "Habitaclia",
        "www.habitaclia.com": "Habitaclia",
        "servihabitat.com": "Servihabitat",
        "www.servihabitat.com": "Servihabitat",
        "fotocasa.es": "Fotocasa",
        "www.fotocasa.es": "Fotocasa",
        "yaencontre.com": "yaencontre",
        "www.yaencontre.com": "yaencontre",
        "thinkspain.com": "ThinkSPAIN",
        "www.thinkspain.com": "ThinkSPAIN",
    }
    return mapping.get(hostname, hostname or "Fuente desconocida")


def _normalize_source_name(value: str, source_url: str = "") -> str:
    hostname = _extract_hostname(source_url)
    if hostname:
        return _source_name_from_hostname(hostname)

    value = (value or "").strip()
    if value.lower() in {"exploracion ia", "ia", "ai", "openai"}:
        return "Fuente desconocida"

    return value or "Fuente desconocida"

def _normalize_source_code(source_name: str, source_url: str) -> str:
    hostname = _extract_hostname(source_url)

    hostname_code_map = {
        "idealista.com": "idealista",
        "pisos.com": "pisos",
        "habitaclia.com": "habitaclia",
        "servihabitat.com": "servihabitat",
        "fotocasa.es": "fotocasa",
        "yaencontre.com": "yaencontre",
        "thinkspain.com": "thinkspain",
    }

    if hostname in hostname_code_map:
        return hostname_code_map[hostname]

    base_name = _normalize_source_name(source_name, source_url)
    code = slugify(base_name)
    if code:
        return code[:50]

    return "fuente-desconocida"


def _get_or_create_real_source(source_name: str, source_url: str) -> Source:
    normalized_name = _normalize_source_name(source_name, source_url)
    code = _normalize_source_code(normalized_name, source_url)
    base_url = _extract_base_url(source_url)

    source, created = Source.objects.get_or_create(
        code=code,
        defaults={
            "name": normalized_name,
            "base_url": base_url,
            "source_type": Source.SourceType.PORTAL,
            "is_active": True,
            "is_verified": False,
        },
    )

    updated = False

    if not source.name and normalized_name:
        source.name = normalized_name
        updated = True

    if base_url and source.base_url != base_url:
        source.base_url = base_url
        updated = True

    if source.source_type != Source.SourceType.PORTAL:
        source.source_type = Source.SourceType.PORTAL
        updated = True

    if not source.is_active:
        source.is_active = True
        updated = True

    if updated:
        source.save(update_fields=["name", "base_url", "source_type", "is_active", "updated_at"])

    return source

def _looks_like_property_detail_url(source_url: str) -> bool:
    if not source_url:
        return False

    parsed = urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        return False

    host = _extract_hostname(source_url)
    path = (parsed.path or "").strip().lower()

    if not path or path == "/":
        return False

    # Regla estricta inicial para Idealista:
    # solo aceptamos fichas reales tipo /inmueble/123456789/
    if host == "idealista.com":
        return "/inmueble/" in path

    # Regla mínima genérica para el resto:
    # evitar URLs raíz/home/listados vacíos muy obvios
    return True


def _build_capture_warning(idx: int, reason: str, source_url: str = "") -> str:
    base = f"Item {idx} descartado: {reason}"
    if source_url:
        return f"{base} | {source_url}"
    return base

def _run_mock_search(search_profile: SearchProfile) -> SearchRun:
    run = SearchRun.objects.create(
        search_profile=search_profile,
        status=SearchRun.Status.RUNNING,
        execution_mode=SearchRun.ExecutionMode.MOCK,
        provider="internal_mock",
        model_name="mock_v1",
        started_at=timezone.now(),
        filters_snapshot={
            "operation_type": search_profile.operation_type,
            "province": search_profile.province,
            "zone": search_profile.zone or "",
            "property_types": search_profile.property_types or [],
            "max_price": str(search_profile.max_price) if search_profile.max_price is not None else None,
            "min_bedrooms": search_profile.min_bedrooms,
            "ai_prompt": search_profile.ai_prompt or "",
        },
    )

    property_types = search_profile.property_types or []
    property_type = property_types[0] if property_types else CapturedProperty.PropertyType.HOUSE

    province = search_profile.province or "Provincia sin definir"
    zone = (search_profile.zone or "").strip()
    municipality = zone or province
    location_text = f"{municipality}, {province}" if zone else province

    max_price = search_profile.max_price or Decimal("60000.00")
    min_bedrooms = search_profile.min_bedrooms or 2

    samples = [
        {
            "title": f"{location_text} · oportunidad 1",
            "price": max_price,
            "bedrooms": min_bedrooms,
            "area_m2": Decimal("85.00"),
            "source_external_id": f"{search_profile.id}-sample-1",
            "source_name": "Idealista",
            "source_url": f"https://www.idealista.com/inmueble/mock-{search_profile.id}-1/",
        },
        {
            "title": f"{location_text} · oportunidad 2",
            "price": max(max_price - Decimal("5000.00"), Decimal("1.00")),
            "bedrooms": min_bedrooms + 1,
            "area_m2": Decimal("102.00"),
            "source_external_id": f"{search_profile.id}-sample-2",
            "source_name": "pisos.com",
            "source_url": f"https://www.pisos.com/comprar/piso-mock-{search_profile.id}-2/",
        },
        {
            "title": f"{location_text} · oportunidad 3",
            "price": max(max_price - Decimal("9000.00"), Decimal("1.00")),
            "bedrooms": min_bedrooms,
            "area_m2": Decimal("76.00"),
            "source_external_id": f"{search_profile.id}-sample-3",
            "source_name": "Servihabitat",
            "source_url": f"https://www.servihabitat.com/es/vivienda/mock-{search_profile.id}-3",
        },
    ]

    total_new = 0
    total_updated = 0

    for sample in samples:
        sample_source = _get_or_create_real_source(
            sample["source_name"],
            sample["source_url"],
        )

        _, created = CapturedProperty.objects.update_or_create(
            source=sample_source,
            source_external_id=sample["source_external_id"],
            defaults={
                "owner": search_profile.owner,
                "search_profile": search_profile,
                "search_run": run,
                "entry_mode": CapturedProperty.EntryMode.AI_EXPLORATION,
                "title": sample["title"],
                "description_raw": (
                    f"Captación de prueba para {search_profile.name} "
                    f"en {location_text}."
                ),
                "province": province,
                "municipality": municipality,
                "property_type": property_type,
                "operation_type": search_profile.operation_type,
                "price": sample["price"],
                "bedrooms": sample["bedrooms"],
                "bathrooms": 1,
                "area_m2": sample["area_m2"],
                "status": CapturedProperty.Status.CAPTURED,
                "review_status": CapturedProperty.ReviewStatus.PENDING,
                "source_url": sample["source_url"],
                "last_seen_at": timezone.now(),
            },
        )
        if created:
            total_new += 1
        else:
            total_updated += 1

    run.status = SearchRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.total_candidates = len(samples)
    run.total_valid_candidates = len(samples)
    run.total_found = len(samples)
    run.total_new = total_new
    run.total_updated = total_updated
    run.total_errors = 0
    run.run_notes = "Ejecución mock controlada."
    run.save()

    return run

def _run_ai_discovery(search_profile: SearchProfile) -> SearchRun:
    client = AIDiscoveryClient()
    result = client.discover(
        operation_type=search_profile.operation_type,
        province=search_profile.province,
        zone=search_profile.zone or "",
        property_types=search_profile.property_types or [],
        max_price=search_profile.max_price,
        min_bedrooms=search_profile.min_bedrooms,
        ai_prompt=search_profile.ai_prompt or "",
    )

    run = SearchRun.objects.create(
        search_profile=search_profile,
        status=SearchRun.Status.RUNNING,
        execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
        provider=result.provider,
        model_name=result.model_name,
        query_text=result.query_text,
        filters_snapshot=result.filters_snapshot,
        raw_response=result.raw_response,
        warnings=result.warnings,
        started_at=timezone.now(),
    )

    total_candidates = len(result.items)
    total_valid_candidates = 0
    total_new = 0
    total_updated = 0
    total_errors = 0

    for idx, item in enumerate(result.items, start=1):
        if not item.source_url:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "sin source_url")
            ]
            continue

        if not _looks_like_property_detail_url(item.source_url):
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "url no parece ficha real", item.source_url)
            ]
            continue

        total_valid_candidates += 1

        source = _get_or_create_real_source(item.source_name, item.source_url)
        
        normalized_url = _normalize_property_url(item.source_url)
        external_id = normalized_url or f"{search_profile.id}-ai-{idx}"
        zone_text = getattr(item, "zone_text", None) or getattr(item, "zone", None) or ""
        possible_duplicate = _has_probable_duplicate(
            owner=search_profile.owner,
            source=source,
            operation_type=search_profile.operation_type,
            property_type=item.property_type or (
                (search_profile.property_types or [CapturedProperty.PropertyType.FLAT])[0]
            ),
            municipality=item.municipality or "",
            price=item.price,
            title=item.title,
            external_id=external_id,
        )

        _, created = CapturedProperty.objects.update_or_create(
            source=source,
            source_external_id=external_id,
            defaults={
                "owner": search_profile.owner,
                "search_profile": search_profile,
                "search_run": run,
                "entry_mode": CapturedProperty.EntryMode.AI_EXPLORATION,
                "title": item.title,
                "description_raw": item.summary,
                "province": item.province or search_profile.province,
                "municipality": item.municipality or "",
                "zone_text": zone_text,
                "property_type": item.property_type or (
                    (search_profile.property_types or [CapturedProperty.PropertyType.FLAT])[0]
                ),
                "operation_type": search_profile.operation_type,
                "price": item.price,
                "bedrooms": item.bedrooms,
                "bathrooms": item.bathrooms,
                "area_m2": item.area_m2,
                "status": CapturedProperty.Status.CAPTURED,
                "review_status": CapturedProperty.ReviewStatus.PENDING,
                "source_url": normalized_url or item.source_url,
                "possible_duplicate": possible_duplicate,
                "last_seen_at": timezone.now(),
            },
        )
        if created:
            total_new += 1
        else:
            total_updated += 1

    run.status = SearchRun.Status.COMPLETED if total_errors == 0 else SearchRun.Status.COMPLETED_WITH_ERRORS
    run.finished_at = timezone.now()
    run.total_candidates = total_candidates
    run.total_valid_candidates = total_valid_candidates
    run.total_found = total_valid_candidates
    run.total_new = total_new
    run.total_updated = total_updated
    run.total_errors = total_errors
    run.run_notes = "Ejecución AI Discovery."
    run.warnings = list(run.warnings or [])
    run.save()

    return run

def run_search_profile(search_profile: SearchProfile) -> SearchRun:
    return _run_ai_discovery(search_profile)