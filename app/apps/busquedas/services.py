from decimal import Decimal
from django.utils import timezone
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from .ai_discovery import AIDiscoveryClient
from .models import SearchProfile, SearchRun
from .price import normalize_euro_price
from apps.ia.usage import can_run_ai_discovery, format_ai_quota_message
from urllib.parse import urlparse, urlunparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.utils.text import slugify
import re
import unicodedata
from difflib import SequenceMatcher

# === SOOI V2.5 · AI Discovery trust gate helpers ===
def _sooi_v25_json_value(value):
    if value is None:
        return None
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except Exception:
        pass
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _sooi_v25_to_float(value):
    normalized = normalize_euro_price(value)
    return float(normalized) if normalized is not None else None


def _sooi_v25_get_local_value(scope, *names):
    for name in names:
        value = scope.get(name)
        if value not in (None, ""):
            return value
    return None


def _sooi_v25_price_in_range(price, min_price=None, max_price=None):
    price_f = _sooi_v25_to_float(price)
    if price_f is None:
        return False
    min_f = _sooi_v25_to_float(min_price)
    max_f = _sooi_v25_to_float(max_price)
    if min_f is not None and price_f < min_f:
        return False
    if max_f is not None and price_f > max_f:
        return False
    return True


def _sooi_v25_reason_text(reason):
    return str(reason or "").strip().lower()


def _sooi_v25_is_hard_unavailable(reason):
    r = _sooi_v25_reason_text(reason)
    hard_terms = (
        "retirado",
        "no disponible",
        "anuncio no disponible",
        "eliminado",
        "removed",
        "deleted",
        "unavailable",
        "expired",
        "404",
        "not found",
        "listado",
        "búsqueda",
        "busqueda",
        "home",
        "homepage",
        "url generica",
        "url genérica",
    )
    return any(term in r for term in hard_terms)


def _sooi_v25_is_soft_non_verifiable(reason):
    r = _sooi_v25_reason_text(reason)
    if _sooi_v25_is_hard_unavailable(r):
        return False
    soft_terms = (
        "403",
        "429",
        "forbidden",
        "too many requests",
        "no verificable",
        "not verifiable",
        "idealista",
        "yaencontre",
        "yaencontré",
        "precio_no_encontrado",
        "precio no encontrado",
        "price_not_found",
        "html",
        "bloqueado",
        "blocked",
    )
    return any(term in r for term in soft_terms)



def _sooi_v25_text_has_unavailable_signal(text):
    if not text:
        return None

    import re
    normalized = re.sub(r"\s+", " ", str(text).lower())

    signals = (
        ("idealista_anuncio_no_publicado", "lo sentimos, este anuncio ya no está publicado"),
        ("idealista_anuncio_no_publicado", "lo sentimos, este anuncio ya no esta publicado"),
        ("anuncio_no_publicado", "este anuncio ya no está publicado"),
        ("anuncio_no_publicado", "este anuncio ya no esta publicado"),
        ("anuncio_dado_de_baja", "lo dio de baja"),
        ("anuncio_dado_de_baja", "dado de baja"),
        ("anuncio_no_disponible", "anuncio no disponible"),
        ("anuncio_retirado", "anuncio retirado"),
        ("inmueble_retirado", "inmueble retirado"),
    )

    for code, phrase in signals:
        if phrase in normalized:
            return code

    return None


def _sr0_is_fotocasa_generic_unavailable(url, status_code, reason):
    host = _extract_hostname(url)
    normalized = _sooi_v25_reason_text(reason)
    return (
        host == "fotocasa.es"
        and int(status_code or 0) == 200
        and normalized in {
            "anuncio no disponible",
            "este anuncio no está disponible",
            "este anuncio no esta disponible",
            "anuncio_no_disponible",
        }
    )


def _sooi_v25_url_has_unavailable_signal(url):
    if not url:
        return None

    try:
        import requests

        response = requests.get(
            url,
            timeout=12,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
        )

        signal = _sooi_v25_text_has_unavailable_signal(response.text)
        if signal:
            if _sr0_is_fotocasa_generic_unavailable(url, response.status_code, signal):
                return None
            return signal

        if response.status_code == 404:
            return "http_404"

        return None

    except Exception:
        # 403/429/timeout no son descarte duro: siguen siendo revisables.
        return None


def _sooi_v25_add_warning(scope, message):
    warnings = scope.get("warnings")
    if isinstance(warnings, list):
        warnings.append(message)


def _sooi_v25_quality_gate(raw_response):
    if not isinstance(raw_response, dict):
        return {
            "verified": 0,
            "reviewable": 0,
            "discarded": 0,
            "total_candidates": 0,
        }

    qg = raw_response.setdefault("quality_gate", {})
    qg.setdefault("version", "sooi_v2_5")
    qg.setdefault("verified", 0)
    qg.setdefault("reviewable", 0)
    qg.setdefault("discarded", 0)
    qg.setdefault("total_candidates", 0)
    return qg


def _sooi_v25_finalize_raw_response(raw_response):
    if not isinstance(raw_response, dict):
        return raw_response

    qg = raw_response.setdefault("quality_gate", {})
    verified = int(qg.get("verified") or 0)
    reviewable = int(qg.get("reviewable") or 0)
    discarded = int(qg.get("discarded") or 0)

    raw_response["total_found"] = verified + reviewable
    raw_response["total_errors"] = discarded
    return raw_response


def _sooi_v25_merge_ai_signals(
    existing,
    *,
    trust_level,
    accepted,
    reviewable,
    validation_reason,
    price_validation_reason,
    ai_price,
    portal_price,
    max_allowed_price,
):
    data = existing if isinstance(existing, dict) else {}
    data = dict(data)

    data.update({
        "version": "sooi_v2_5",
        "trust_level": trust_level,
        "accepted": bool(accepted),
        "reviewable": bool(reviewable),
        "validation_reason": validation_reason,
        "price_validation_reason": price_validation_reason,
        "ai_price": _sooi_v25_json_value(ai_price),
        "portal_price": _sooi_v25_json_value(portal_price),
        "max_allowed_price": _sooi_v25_json_value(max_allowed_price),
    })
    return data

# === /SOOI V2.5 ===


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

    if host == "solvia.es" or host.endswith(".solvia.es"):
        # Ficha real Solvia:
        # /es/propiedades/comprar/piso-almeria-3-dormitorios-164993-202819
        # Rechaza listados como /es/comprar/viviendas/cordoba?...
        return (
            path.startswith("/es/propiedades/comprar/")
            and re.search(r"-\d{5,}-\d{5,}$", path) is not None
        )

    if host == "altamirainmuebles.com" or host.endswith(".altamirainmuebles.com"):
        # Ficha real Altamira:
        # /venta-de-piso/almeria/vicar/segunda-mano/29000944/208591/1
        # Rechaza listados o páginas sin identificadores finales.
        return re.search(
            r"/(?:venta|alquiler)-de-[^/]+/.+/(?:segunda-mano|obra-nueva)/\d{5,}/\d{5,}/\d+$",
            path,
        ) is not None

    if host == "yaencontre.com" or host.endswith(".yaencontre.com"):
        # Rechazar páginas de listado/búsqueda como:
        # /alquiler/pisos/torremolinos
        # /venta/casas/malaga
        # Solo aceptamos si la URL contiene un identificador largo compatible con ficha.
        if path.startswith(("/alquiler/", "/venta/", "/comprar/")):
            return re.search(r"\d{5,}", path) is not None

        return False

    # Para portales no modelados explícitamente, mejor no aceptar automáticamente.
    return False


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

    if not normalized_url:
        return False, "sin source_url"

    if not _looks_like_property_detail_url(normalized_url):
        return False, "url no parece ficha real"

    status_code, final_url, html, error = _fetch_url_probe(normalized_url)

    # Errores concluyentes: el anuncio/recurso no existe.
    if status_code in {404, 410}:
        return False, f"http {status_code}"

    # 400 suele indicar URL mal formada o recurso inválido.
    if status_code == 400:
        return False, "http 400"

    text = (html or "").lower()
    text_no_accents = (
        text
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
    )

    retired_markers = [
        "anuncio no disponible",
        "este anuncio no está disponible",
        "este anuncio no esta disponible",
        "anuncio no encontrado",
        "lo sentimos, este anuncio ya no está publicado",
        "lo sentimos este anuncio ya no está publicado",
        "anuncio ya no está publicado",
        "este anuncio ya no está publicado",
        "ya no está publicado",
        "el anunciante lo dio de baja",
        "lo sentimos, este anuncio ya no esta publicado",
        "lo sentimos este anuncio ya no esta publicado",
        "anuncio ya no esta publicado",
        "este anuncio ya no esta publicado",
        "ya no esta publicado",
        "el anunciante lo dio de baja",
    ]

    matched_markers = [
        marker for marker in retired_markers
        if marker in text or marker in text_no_accents
    ]
    if matched_markers and _sr0_is_fotocasa_generic_unavailable(
        normalized_url, status_code, matched_markers[0]
    ):
        return False, "fotocasa http 200 no verificable por texto genérico"

    if matched_markers:
        return False, "anuncio retirado o no publicado"

    host = _extract_hostname(normalized_url)

    # Idealista puede bloquear la validación HTTP desde servidor aunque la URL sea una ficha real.
    # No intentamos saltar el bloqueo: aceptamos la captación como pendiente de revisión manual
    # siempre que previamente haya pasado la validación estructural de URL (/inmueble/<id>/).
    if status_code == 403 and "idealista." in host:
        return False, "idealista no verificable desde servidor"

    # Si otros portales bloquean o limitan la petición, no descartamos para evitar falsos negativos.
    if status_code in {401, 403, 429}:
        return False, f"validación no concluyente: http {status_code}"

    if status_code >= 500:
        return False, f"validación no concluyente: http {status_code}"

    if status_code == 0 and error:
        return False, f"validación no concluyente: {error}"

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

def _quality_gate_decimal(value):
    return normalize_euro_price(value)


def _extract_portal_price_for_quality_gate(url: str):
    """
    Devuelve (precio_decimal, razon).
    Regla operativa: si no se puede confirmar precio real, no se debe guardar captación automática.
    """
    import re
    from decimal import Decimal, InvalidOperation
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if not url:
        return None, "sin_url"

    def to_decimal(raw):
        if raw is None:
            return None

        s = str(raw)
        s = s.replace("\\xa0", " ").strip()
        s = re.sub(r"[^0-9,\\.]", "", s)

        if not s:
            return None

        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            if re.search(r",\\d{1,2}$", s):
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "." in s:
            if not re.search(r"\\.\\d{1,2}$", s):
                s = s.replace(".", "")

        try:
            value = Decimal(s)
        except (InvalidOperation, ValueError, TypeError):
            return None

        if value <= 0 or value > Decimal("10000000"):
            return None

        return value

    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
            },
        )

        with urlopen(req, timeout=10) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status and int(status) >= 400:
                return None, f"http_{status}"

            raw = response.read(2_000_000)

    except HTTPError as exc:
        return None, f"http_{exc.code}"
    except URLError as exc:
        return None, f"url_error_{exc.reason}"
    except Exception as exc:
        return None, f"fetch_error_{exc.__class__.__name__}"

    html = raw.decode("utf-8", errors="ignore")
    html = html.replace("&euro;", "€").replace("&#8364;", "€")
    lower = html.lower()

    retired_phrases = [
        "anuncio no disponible",
        "este anuncio ya no está disponible",
        "este anuncio ya no esta disponible",
        "anuncio retirado",
        "publicación no disponible",
        "publicacion no disponible",
        "página no encontrada",
        "pagina no encontrada",
        "no encontramos el anuncio",
    ]

    if any(phrase in lower for phrase in retired_phrases):
        return None, "anuncio_no_disponible"

    patterns = [
        r'property=["\\\']product:price:amount["\\\'][^>]+content=["\\\']([0-9][0-9\\., ]{1,14})["\\\']',
        r'name=["\\\']product:price:amount["\\\'][^>]+content=["\\\']([0-9][0-9\\., ]{1,14})["\\\']',
        r'"price"\\s*:\\s*"?([0-9][0-9\\., ]{1,14})"?',
        r'"amount"\\s*:\\s*"?([0-9][0-9\\., ]{1,14})"?',
        r'([0-9]{1,3}(?:[\\.\\s][0-9]{3})+|[0-9]{3,6})(?:,[0-9]{1,2})?\\s*(?:€|eur)\\s*(?:/\\s*)?(?:mes|month)?',
    ]

    seen = set()

    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.I):
            value = to_decimal(match.group(1))
            if value is None:
                continue

            key = str(value)
            if key in seen:
                continue

            seen.add(key)

            # Para SOOI operativo, ignoramos importes demasiado bajos que suelen ser cuotas, refs o basura.
            if value < Decimal("100"):
                continue

            return value, "precio_extraido_portal"

    return None, "precio_no_encontrado_en_html"


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

    from .commercial_metering import commercial_metering_enabled
    commercially_authorized = bool(
        commercial_metering_enabled() and run.governance_enabled
        and run.budget_reserved_credits is not None
    )
    allowed, usage = (True, None) if commercially_authorized else can_run_ai_discovery(
        search_profile.owner, exclude_run_id=run.id,
    )
    if not allowed:
        message = format_ai_quota_message(usage)
        run.status = SearchRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.provider = "sooi_quota"
        run.model_name = "monthly_ai_quota"
        run.total_candidates = 0
        run.total_valid_candidates = 0
        run.total_found = 0
        run.total_new = 0
        run.total_updated = 0
        run.total_errors = 0
        run.error_message = ""
        run.warnings = [message]
        run.run_notes = "Ejecución IA no realizada por cupo mensual del plan. No se marca como fallida."
        run.save(update_fields=[
            "status",
            "finished_at",
            "provider",
            "model_name",
            "total_candidates",
            "total_valid_candidates",
            "total_found",
            "total_new",
            "total_updated",
            "total_errors",
            "error_message",
            "warnings",
            "run_notes",
            "updated_at",
        ])
        return run

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
        run.status = SearchRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.total_candidates = 0
        run.total_valid_candidates = 0
        run.total_found = 0
        run.total_new = 0
        run.total_updated = 0
        run.total_errors = 0
        run.error_message = ""
        run.run_notes = "Ejecución IA sin captaciones: proveedor no disponible, sin saldo, sin cuota o sin proveedor alternativo. No se marca como fallida."
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

    # SOOI V2.5: raw_response inicializado antes del quality gate
    raw_response = run.raw_response if isinstance(run.raw_response, dict) else {"raw": run.raw_response}
    if raw_response is None:
        raw_response = {}

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

        quality_gate_warnings = []

        if item.price is None:
            quality_gate_warnings.append("precio_no_verificado_desde_portal")
            run.warnings = list(run.warnings or []) + [
                _build_capture_warning(idx, "precio no verificado desde portal", item.source_url)
            ]

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
        sooi_v25_quality_gate = _sooi_v25_quality_gate(raw_response)
        sooi_v25_quality_gate["total_candidates"] += 1
        sooi_v25_min_allowed_price = _sooi_v25_get_local_value(locals(), "min_price", "min_allowed_price", "precio_min", "precio_desde")
        sooi_v25_max_allowed_price = _sooi_v25_get_local_value(locals(), "max_price", "max_allowed_price", "precio_max", "precio_hasta")
        sooi_v25_trust_level = "verified"
        sooi_v25_target_status = CapturedProperty.Status.CAPTURED
        sooi_v25_accepted = True
        sooi_v25_reviewable = False
        sooi_v25_validation_reason = validation_reason
        sooi_v25_price_validation_reason = None
        sooi_v25_portal_price_for_signals = None

        if not is_valid_url:
            if (
                _sooi_v25_is_soft_non_verifiable(validation_reason)
                and _sooi_v25_price_in_range(getattr(item, "price", None), sooi_v25_min_allowed_price, sooi_v25_max_allowed_price)
            ):
                sooi_v25_trust_level = "reviewable"
                sooi_v25_target_status = CapturedProperty.Status.IN_REVIEW
                sooi_v25_accepted = False
                sooi_v25_reviewable = True
                sooi_v25_validation_reason = validation_reason or "url_no_verificable"
                _sooi_v25_add_warning(
                    locals(),
                    f"SOOI V2.5 reviewable: URL no verificable pero ficha candidata razonable: {getattr(item, 'url', '')} · razón={sooi_v25_validation_reason}"
                )
            else:
                sooi_v25_quality_gate["discarded"] += 1
                _sooi_v25_add_warning(
                    locals(),
                    f"SOOI V2.5 descartado: URL no válida/no individual/no disponible: {getattr(item, 'url', '')} · razón={validation_reason}"
                )
                continue
        if validation_reason != "ok":
            quality_gate_warnings.append(f"url_validacion_no_concluyente: {validation_reason}")

        item_price_for_save = item.price
        portal_price = None
        price_validation_reason = "sin_max_price_configurado"

        max_price = _quality_gate_decimal(getattr(search_profile, "max_price", None))
        min_price = _quality_gate_decimal(getattr(search_profile, "min_price", None))

        if max_price is not None:
            portal_price, price_validation_reason = _extract_portal_price_for_quality_gate(validation_url)

            if portal_price is None:
                sooi_v25_price_validation_reason = price_validation_reason or "precio_no_encontrado_en_html"
                sooi_v25_portal_price_for_signals = None

                sooi_v25_unavailable_reason = _sooi_v25_url_has_unavailable_signal(validation_url)
                if sooi_v25_unavailable_reason:
                    sooi_v25_quality_gate["discarded"] += 1
                    _sooi_v25_add_warning(
                        locals(),
                        f"SOOI V2.5 descartado: anuncio no publicado/retirado: {getattr(item, 'url', '')} · razón={sooi_v25_unavailable_reason}"
                    )
                    continue

                if (
                    _sooi_v25_is_soft_non_verifiable(sooi_v25_price_validation_reason)
                    and _sooi_v25_price_in_range(getattr(item, "price", None), sooi_v25_min_allowed_price, sooi_v25_max_allowed_price)
                    and not _sooi_v25_is_hard_unavailable(sooi_v25_validation_reason)
                ):
                    sooi_v25_trust_level = "reviewable"
                    sooi_v25_target_status = CapturedProperty.Status.IN_REVIEW
                    sooi_v25_accepted = False
                    sooi_v25_reviewable = True

                    # Permitimos que el flujo existente continúe usando precio IA,
                    # pero en ai_signals dejamos portal_price=None.
                    portal_price = getattr(item, "price", None)

                    _sooi_v25_add_warning(
                        locals(),
                        f"SOOI V2.5 reviewable: precio portal no verificable; se usa precio IA para revisión humana: {getattr(item, 'url', '')} · razón={sooi_v25_price_validation_reason}"
                    )
                else:
                    sooi_v25_quality_gate["discarded"] += 1
                    _sooi_v25_add_warning(
                        locals(),
                        f"SOOI V2.5 descartado: precio portal no verificable y candidato no revisable: {getattr(item, 'url', '')} · razón={sooi_v25_price_validation_reason}"
                    )
                    continue
            else:
                sooi_v25_portal_price_for_signals = portal_price
                sooi_v25_price_validation_reason = price_validation_reason if "price_validation_reason" in locals() else None
            if min_price is not None and portal_price < min_price:
                total_errors += 1
                run.warnings = list(run.warnings or []) + [
                    _build_capture_warning(
                        idx,
                        f"precio portal por debajo del mínimo ({portal_price} < {min_price})",
                        validation_url,
                    )
                ]
                continue

            if portal_price > max_price:
                total_errors += 1
                run.warnings = list(run.warnings or []) + [
                    _build_capture_warning(
                        idx,
                        f"precio portal por encima del máximo ({portal_price} > {max_price})",
                        validation_url,
                    )
                ]
                continue

            ai_price = _quality_gate_decimal(item.price)
            if ai_price is not None and ai_price != portal_price:
                quality_gate_warnings.append(
                    f"precio_ia_difiere_portal: ia={ai_price} portal={portal_price}"
                )

            item_price_for_save = portal_price

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
            price=item_price_for_save,
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
            "price": item_price_for_save,
            "bedrooms": item.bedrooms,
            "bathrooms": item.bathrooms,
            "area_m2": item.area_m2,
            "status": sooi_v25_target_status,
            "review_status": CapturedProperty.ReviewStatus.PENDING,
            "source_url": normalized_url or item.source_url,
            "possible_duplicate": possible_duplicate,
            "ai_signals": {
                **_sooi_v25_merge_ai_signals(
                    None,
                    trust_level=sooi_v25_trust_level,
                    accepted=sooi_v25_accepted,
                    reviewable=sooi_v25_reviewable,
                    validation_reason=sooi_v25_validation_reason,
                    price_validation_reason=sooi_v25_price_validation_reason,
                    ai_price=getattr(item, "price", None),
                    portal_price=sooi_v25_portal_price_for_signals,
                    max_allowed_price=sooi_v25_max_allowed_price,
                ),
                "type": "quality_gate",
                "warnings": quality_gate_warnings,
                "source_url_checked": validation_url,
            },
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

        sooi_v25_quality_gate["reviewable" if sooi_v25_reviewable else "verified"] += 1

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

    if "raw_response" not in locals() or not isinstance(raw_response, dict):
        raw_response = run.raw_response if isinstance(run.raw_response, dict) else {"raw": run.raw_response}

    sooi_v25_quality_gate = _sooi_v25_quality_gate(raw_response)
    sooi_v25_quality_gate["version"] = "sooi_v2_5"
    sooi_v25_quality_gate["total_candidates"] = total_candidates
    sooi_v25_quality_gate["warnings_count"] = len(run.warnings or [])

    _sooi_v25_finalize_raw_response(raw_response)

    run.total_found = raw_response.get("total_found", 0)
    run.total_errors = raw_response.get("total_errors", 0)
    run.raw_response = raw_response

    run.warnings = list(run.warnings or [])
    run.save()

    return run



def _sooi_env_flag(name: str, default: str = "0") -> bool:
    import os
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _sooi_env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    import os
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _sooi_v2614_flag_enabled() -> bool:
    return _sooi_env_flag("SOOI_USE_HYBRID_V2614", "0")


def _sooi_v2614_write_enabled() -> bool:
    # Segundo seguro: permite probar el motor normal sin escribir captaciones.
    return _sooi_env_flag("SOOI_HYBRID_V2614_WRITE", "0")



def _sooi_json_safe_v2614(value):
    import json
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _sr0_is_low_coverage(totals, total_candidates, total_valid):
    attempted = int((totals or {}).get("attempted") or 0)
    failed = int((totals or {}).get("failed") or 0)
    discarded = int((totals or {}).get("discarded") or 0)
    valid = int(total_valid or 0)
    candidates = int(total_candidates or 0)
    return (
        attempted > 0
        and valid <= 1
        and (failed > 0 or discarded > 0 or candidates > valid)
    )

def _run_hybrid_discovery_v2614(
    search_profile: SearchProfile, run: SearchRun | None = None, *, write_override=None,
    timeout_override=None, max_results_override=None, use_ai_override=None,
) -> SearchRun:
    from django.utils import timezone
    from apps.busquedas.services_hybrid_coverage_v261 import run_hybrid_discovery_v261

    if run is None:
        run = SearchRun.objects.create(
            search_profile=search_profile,
            status=SearchRun.Status.RUNNING,
            execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
            started_at=timezone.now(),
        )
    else:
        # Celery redelivery/replay of a completed durable analysis must not
        # turn into a second paid discovery.
        if (
            run.governance_enabled
            and run.status in {SearchRun.Status.COMPLETED, SearchRun.Status.COMPLETED_WITH_ERRORS}
            and isinstance(run.raw_response, dict)
            and isinstance(run.raw_response.get("source_coverage"), list)
        ):
            return run
        run.status = SearchRun.Status.RUNNING
        run.execution_mode = SearchRun.ExecutionMode.AI_DISCOVERY
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["status", "execution_mode", "started_at", "updated_at"])

    timeout = _sooi_env_int("SOOI_HYBRID_V2614_TIMEOUT", 12, 4, 30)
    max_results = _sooi_env_int("SOOI_HYBRID_V2614_MAX_RESULTS", 8, 1, 20)
    use_ai = _sooi_env_flag("SOOI_HYBRID_V2614_USE_AI", "1")
    if timeout_override is not None:
        timeout = int(timeout_override)
    if max_results_override is not None:
        max_results = int(max_results_override)
    if use_ai_override is not None:
        use_ai = bool(use_ai_override)
    write = _sooi_v2614_write_enabled() if write_override is None else bool(write_override)

    version_label = "V2.6.1"
    try:
        result = run_hybrid_discovery_v261(
            search_profile.id,
            timeout=timeout,
            max_results_per_source=max_results,
            use_ai=use_ai,
            write=write,
            search_run=run,
        )
    except Exception as exc:
        run.status = SearchRun.Status.FAILED
        run.finished_at = timezone.now()
        run.provider = "sooi_hybrid_v2614"
        run.model_name = version_label
        run.error_message = str(exc)
        run.warnings = [f"Error en SOOI Hybrid V2.6.1.4: {exc}"]
        run.run_notes = "Ejecución híbrida V2.6.1.4 fallida."
        run.save(update_fields=[
            "status", "finished_at", "provider", "model_name",
            "error_message", "warnings", "run_notes", "updated_at",
        ])
        return run

    totals = result.get("totals") or {}
    action_totals = (result.get("action_plan") or {}).get("totals") or {}
    write_result = result.get("write_result") or {}
    version_label = (result.get("version") or "V2.6.1").replace("-dry-run", "").replace("-write", "")

    source_coverage = result.get("source_coverage") or []
    total_candidates = sum(int(row.get("candidate_count") or 0) for row in source_coverage)
    quality_semantics = result.get("quality_semantics") or {}
    total_valid = int(quality_semantics.get("actionable_count") or totals.get("actionable") or 0)

    warnings = []
    if not result.get("is_complete"):
        warnings.append(f"SOOI {version_label}: ejecución incompleta; no se intentaron todas las fuentes aplicables.")
    if int(action_totals.get("skip_existing_opportunity_unavailable") or 0):
        warnings.append(f"SOOI {version_label}: hay oportunidades existentes con anuncio no verificable/no disponible; no se modificaron automáticamente.")
    if not write:
        warnings.append(f"SOOI {version_label} ejecutado en modo sin escritura por SOOI_HYBRID_V2614_WRITE=0.")

    low_coverage = _sr0_is_low_coverage(totals, total_candidates, total_valid)
    if low_coverage:
        warnings.append(
            "SOOI_SEARCH_LOW_COVERAGE: la ejecución terminó, pero la cobertura "
            "o validación fue insuficiente; cero resultados no implica ausencia "
            "de oferta en el mercado."
        )

    run.status = (
        SearchRun.Status.COMPLETED_WITH_ERRORS
        if low_coverage else SearchRun.Status.COMPLETED
    )
    run.finished_at = timezone.now()
    run.provider = "sooi_hybrid_v2614"
    run.model_name = version_label
    run.query_text = f"SOOI Hybrid V2.6.1.4 · profile={search_profile.id} · {search_profile.name}"
    run.filters_snapshot = _sooi_json_safe_v2614(result.get("context") or {})
    run.raw_response = _sooi_json_safe_v2614(result)
    run.warnings = warnings
    run.total_candidates = total_candidates
    run.total_valid_candidates = total_valid
    run.total_found = total_valid
    run.total_new = int(write_result.get("created") or 0)
    run.total_updated = int(write_result.get("updated") or 0)
    run.total_errors = int(totals.get("failed") or 0)
    run.error_message = ""
    run.run_notes = (
        f"Ejecución SOOI Hybrid {version_label} "
        f"write={write}; attempted={totals.get('attempted')}; "
        f"discarded={totals.get('discarded')}; "
        f"new={run.total_new}; updated={run.total_updated}."
    )
    # SR0.3A.2 only projects the already-decided SR0.3A.1 provider outcome.
    # It neither adds calls nor changes source planning.
    from .searchrun_governance import project_hard_provider_stop
    project_hard_provider_stop(run, source_coverage)
    run.save()
    return run

def run_search_profile(search_profile: SearchProfile, run: SearchRun | None = None) -> SearchRun:
    # A governed browser authorization always enters the governed planner.
    # Browser/provider/model toggles cannot route around its mode and cap.
    if run is not None and run.governance_enabled:
        return _run_hybrid_discovery_v2614(
            search_profile, run=run,
            use_ai_override=run.search_mode != SearchRun.SearchMode.FREE,
        )
    if _sooi_v2614_flag_enabled():
        return _run_hybrid_discovery_v2614(search_profile, run=run)
    return _run_ai_discovery(search_profile, run=run)
