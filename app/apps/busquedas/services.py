from decimal import Decimal
from django.utils import timezone
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from .ai_discovery import AIDiscoveryClient
from .models import SearchProfile, SearchRun
from urllib.parse import urlparse, urlunparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
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


def _is_trusted_property_portal_url(source_url: str) -> bool:
    host = _extract_hostname(source_url)
    trusted_hosts = {
        "idealista.com",
        "fotocasa.es",
        "habitaclia.com",
        "pisos.com",
        "servihabitat.com",
        "solvia.es",
        "altamirainmuebles.com",
        "yaencontre.com",
    }

    if host in trusted_hosts:
        return True

    return any(
        host.endswith("." + trusted_host)
        for trusted_host in trusted_hosts
    )


def _looks_like_property_detail_url(source_url: str) -> bool:
    if not source_url:
        return False

    parsed = urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        return False

    host = _extract_hostname(source_url)
    path = (parsed.path or "").strip().lower().rstrip("/")

    if not path or path == "/":
        return False

    if host == "idealista.com":
        return re.search(r"/(?:[a-z]{2}/)?inmueble/\d+", path) is not None

    if host == "fotocasa.es":
        return re.search(r"/\d+/d$", path) is not None

    if host == "habitaclia.com" or host.endswith(".habitaclia.com"):
        # Ficha real Habitaclia suele incluir id tipo -i123456789.htm.
        # URLs como /rent-cartama.htm son listados/zona.
        return re.search(r"-i\d+\.htm$", path) is not None

    if host == "pisos.com":
        return (
            ("/comprar/" in path or "/alquilar/" in path)
            and re.search(r"[_-]\d{5,}(?:_\d{3,})?$", path) is not None
        )

    if host == "servihabitat.com":
        return (
            ("/venta/" in path or "/alquiler/" in path)
            and re.search(r"/\d{6,}$", path) is not None
        )

    return True


def _fetch_url_probe(source_url: str) -> tuple[int, str, str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 SOOI/2.0 URLValidator",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    request = Request(source_url, headers=headers)

    try:
        with urlopen(request, timeout=5) as response:
            status = getattr(response, "status", response.getcode())
            final_url = response.geturl()
            raw = response.read(250000)
            html = raw.decode("utf-8", errors="ignore").lower()
            return status, final_url, html, ""
    except HTTPError as exc:
        try:
            raw = exc.read(120000)
            html = raw.decode("utf-8", errors="ignore").lower()
        except Exception:
            html = ""
        return exc.code, source_url, html, ""
    except (URLError, TimeoutError, ValueError) as exc:
        return 0, source_url, "", exc.__class__.__name__


def _validate_property_source_url(source_url: str) -> tuple[bool, str]:
    normalized_url = _normalize_property_url(source_url) or source_url

    if not _looks_like_property_detail_url(normalized_url):
        return False, "url no parece ficha real"

    status_code, final_url, html, error = _fetch_url_probe(normalized_url)

    if error:
        if _is_trusted_property_portal_url(normalized_url):
            return True, f"ok: ficha real de portal conocido, no verificable automáticamente por {error}"
        return False, f"bloqueada o no verificable: {error}"

    if status_code in {404, 410}:
        return False, f"http {status_code}: página inexistente"

    if status_code >= 400:
        if status_code == 403:
            if _is_trusted_property_portal_url(normalized_url):
                return True, "ok: ficha real de portal conocido, bloqueada por protección anti-bot"
            return False, "no verificable por bloqueo del portal"
        return False, f"http {status_code}: no accesible"

    final_normalized_url = _normalize_property_url(final_url) or final_url
    if final_normalized_url and not _looks_like_property_detail_url(final_normalized_url):
        return False, "redirige a una página que no parece ficha real"

    html = html or ""

    if len(html.strip()) < 500:
        return False, "contenido insuficiente"

    unpublished_markers = [
        "este anuncio ya no está publicado",
        "este anuncio ya no esta publicado",
        "anuncio ya no está publicado",
        "anuncio ya no esta publicado",
        "el anunciante lo dio de baja",
        "no corresponde a ninguna página",
        "no corresponde a ninguna pagina",
        "does not correspond to any page",
        "page not found",
        "not found",
        "404",
    ]

    if any(marker in html for marker in unpublished_markers):
        return False, "anuncio no publicado o página inexistente"

    listing_markers = [
        ("guardar búsqueda", "ordenar por"),
        ("guardar busqueda", "ordenar por"),
        ("resultados", "ordenar por"),
        ("alquiler de pisos en", "resultados"),
        ("venta de pisos en", "resultados"),
        ("alquiler de casas en", "resultados"),
        ("venta de casas en", "resultados"),
        ("búsqueda clásica", "búsqueda por ia"),
        ("busqueda clasica", "busqueda por ia"),
        ("ver mapa", "guardar búsqueda"),
        ("ver mapa", "guardar busqueda"),
    ]

    for marker_a, marker_b in listing_markers:
        if marker_a in html and marker_b in html:
            return False, "página de listado o búsqueda"

    active_hints = [
        "€",
        "m²",
        "m2",
        "habitaciones",
        "dormitorios",
        "baño",
        "baños",
        "contactar",
        "referencia",
    ]

    if not any(marker in html for marker in active_hints):
        return False, "sin señales mínimas de anuncio activo"

    return True, "ok"


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
            "min_price": str(search_profile.min_price) if search_profile.min_price is not None else None,
            "max_price": str(search_profile.max_price) if search_profile.max_price is not None else None,
            "min_area_m2": str(search_profile.min_area_m2) if search_profile.min_area_m2 is not None else None,
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

def _run_ai_discovery(search_profile: SearchProfile, run: SearchRun | None = None) -> SearchRun:
    if run is None:
        run = SearchRun.objects.create(
            search_profile=search_profile,
            status=SearchRun.Status.RUNNING,
            execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
            started_at=timezone.now(),
        )
    else:
        run.status = SearchRun.Status.RUNNING
        run.execution_mode = SearchRun.ExecutionMode.AI_DISCOVERY
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["status", "execution_mode", "started_at", "updated_at"])

    try:
        client = AIDiscoveryClient()
        result = client.discover(
            operation_type=search_profile.operation_type,
            province=search_profile.province,
            zone=search_profile.zone or "",
            property_types=search_profile.property_types or [],
            min_price=search_profile.min_price,
            max_price=search_profile.max_price,
            min_area_m2=search_profile.min_area_m2,
            min_bedrooms=search_profile.min_bedrooms,
            ai_prompt=search_profile.ai_prompt or "",
        )
    except Exception as exc:
        run.status = SearchRun.Status.FAILED
        run.finished_at = timezone.now()
        run.error_message = str(exc)
        run.warnings = [f"Error inesperado en ejecución IA: {exc}"]
        run.save(update_fields=["status", "finished_at", "error_message", "warnings", "updated_at"])
        return run

    run.provider = result.provider
    run.model_name = result.model_name
    run.query_text = result.query_text
    run.filters_snapshot = result.filters_snapshot
    run.raw_response = result.raw_response
    run.warnings = result.warnings
    run.save(update_fields=[
        "provider",
        "model_name",
        "query_text",
        "filters_snapshot",
        "raw_response",
        "warnings",
        "updated_at",
    ])

    provider_statuses = [
        search.get("status")
        for search in (result.raw_response or {}).get("searches", [])
    ]
    blocking_statuses = {"quota", "config", "unsupported", "provider_error", "rate_limit"}

    if not result.items and (
        result.provider == "unconfigured"
        or any(status in blocking_statuses for status in provider_statuses)
    ):
        run.status = SearchRun.Status.FAILED
        run.finished_at = timezone.now()
        run.total_candidates = 0
        run.total_valid_candidates = 0
        run.total_found = 0
        run.total_new = 0
        run.total_updated = 0
        run.total_errors = 0
        run.error_message = "; ".join(run.warnings or [])[:2000]
        run.run_notes = "Ejecución IA no realizada por incidencia del proveedor."
        run.save(update_fields=[
            "status",
            "finished_at",
            "total_candidates",
            "total_valid_candidates",
            "total_found",
            "total_new",
            "total_updated",
            "total_errors",
            "error_message",
            "run_notes",
            "updated_at",
        ])
        return run

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

        if search_profile.min_price is not None and item.price is None:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "precio no informado y hay precio mínimo configurado", item.source_url)
            ]
            continue

        if search_profile.max_price is not None and item.price is None:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "precio no informado y hay precio máximo configurado", item.source_url)
            ]
            continue

        if search_profile.min_price is not None and item.price is not None and item.price < search_profile.min_price:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, f"precio inferior al mínimo configurado ({item.price} < {search_profile.min_price})", item.source_url)
            ]
            continue

        if search_profile.max_price is not None and item.price is not None and item.price > search_profile.max_price:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, f"precio superior al máximo configurado ({item.price} > {search_profile.max_price})", item.source_url)
            ]
            continue

        if search_profile.min_bedrooms is not None and item.bedrooms is None:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "dormitorios no informados y hay mínimo configurado", item.source_url)
            ]
            continue

        if search_profile.min_bedrooms is not None and item.bedrooms is not None and item.bedrooms < search_profile.min_bedrooms:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, f"dormitorios por debajo del mínimo ({item.bedrooms} < {search_profile.min_bedrooms})", item.source_url)
            ]
            continue

        if search_profile.min_area_m2 is not None and item.area_m2 is None:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "superficie no informada y hay metros mínimos configurados", item.source_url)
            ]
            continue

        if search_profile.min_area_m2 is not None and item.area_m2 is not None and item.area_m2 < search_profile.min_area_m2:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, f"superficie inferior al mínimo ({item.area_m2} < {search_profile.min_area_m2})", item.source_url)
            ]
            continue

        allowed_types = search_profile.property_types or []
        if allowed_types and item.property_type not in allowed_types:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, f"tipología fuera de filtros ({item.property_type})", item.source_url)
            ]
            continue

        validation_url = _normalize_property_url(item.source_url) or item.source_url
        is_valid_url, validation_reason = _validate_property_source_url(validation_url)

        if not is_valid_url:
            total_errors += 1
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(
                    idx,
                    f"url descartada: {validation_reason}",
                    validation_url,
                )
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

        defaults = {
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
        }

        existing = CapturedProperty.objects.filter(
            source=source,
            source_external_id=external_id,
            owner=search_profile.owner,
        ).first()

        if existing is None and normalized_url:
            for candidate in CapturedProperty.objects.filter(
                source=source,
                owner=search_profile.owner,
            ).only("id", "source_url", "source_external_id"):
                if _normalize_property_url(candidate.source_url) == normalized_url:
                    existing = candidate
                    break

        if existing is not None:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.source_external_id = external_id
            existing.save()
            created = False
        else:
            CapturedProperty.objects.create(
                source=source,
                source_external_id=external_id,
                **defaults,
            )
            created = True

        if created:
            total_new += 1
        else:
            total_updated += 1

    run.status = SearchRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.total_candidates = total_candidates
    run.total_valid_candidates = total_valid_candidates
    run.total_found = total_valid_candidates
    run.total_new = total_new
    run.total_updated = total_updated
    run.total_errors = total_errors
    run.run_notes = "Ejecución AI Discovery en segundo plano."
    run.warnings = list(run.warnings or [])
    run.save()

    return run


def run_search_profile(search_profile: SearchProfile, run: SearchRun | None = None) -> SearchRun:
    return _run_ai_discovery(search_profile, run=run)