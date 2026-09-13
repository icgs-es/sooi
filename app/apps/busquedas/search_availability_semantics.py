"""Pure strong listing availability evidence for future search runs (SR0.15C)."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse


AVAILABLE_CONFIRMED = "confirmed"
AVAILABLE_UNKNOWN = "unknown"
UNAVAILABLE_CONFIRMED = "unavailable"

_LISTING_TYPES = frozenset({
    "realestatelisting", "apartment", "house", "singlefamilyresidence",
    "residence", "accommodation",
})
_GENERIC_PATH_MARKERS = (
    "/buscar", "/search", "/mapa", "/category", "/categoria",
    "/alquiler-viviendas", "/venta-viviendas", "/todas-las-zonas",
)


def _evidence(state: str, signal: str, source: str) -> dict[str, str]:
    return {"state": state, "signal": signal, "source": source}


def _normalized_location(url: Any) -> tuple[str, str]:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return host, path.casefold()


def _listing_ids(url: Any) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{3,}(?!\d)", urlparse(str(url or "")).path))


def _is_generic_location(url: Any) -> bool:
    _host, path = _normalized_location(url)
    return path == "/" or any(marker in path for marker in _GENERIC_PATH_MARKERS)


def _same_listing(requested_url: str, final_url: str, structured_url: str) -> bool:
    requested = _normalized_location(requested_url)
    final = _normalized_location(final_url or requested_url)
    structured = _normalized_location(structured_url)
    if not structured[0] or _is_generic_location(structured_url):
        return False
    if structured == requested:
        return True
    common_ids = _listing_ids(requested_url) & _listing_ids(final_url) & _listing_ids(structured_url)
    return bool(common_ids and structured == final)


def _json_ld_documents(html: str) -> Iterable[Any]:
    pattern = r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>"
    for match in re.finditer(pattern, str(html or ""), flags=re.I | re.S):
        try:
            yield json.loads(match.group(1).strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue


def _objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _types(value: Any) -> set[str]:
    raw = value if isinstance(value, list) else [value]
    return {str(item or "").rsplit("/", 1)[-1].casefold() for item in raw}


def _has_in_stock(value: Any) -> bool:
    return any(
        str(obj.get("availability") or "").rstrip("/").rsplit("/", 1)[-1].casefold() == "instock"
        for obj in _objects(value)
    )


def _structured_urls(obj: dict[str, Any], base_url: str) -> Iterable[str]:
    for key in ("url", "@id"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            yield urljoin(base_url, value.strip())
    main = obj.get("mainEntityOfPage")
    if isinstance(main, str):
        yield urljoin(base_url, main)
    elif isinstance(main, dict):
        for key in ("url", "@id"):
            if isinstance(main.get(key), str):
                yield urljoin(base_url, main[key])


def _strong_structured_listing(requested_url: str, final_url: str, html: str) -> bool:
    for document in _json_ld_documents(html):
        for obj in _objects(document):
            if not (_types(obj.get("@type")) & _LISTING_TYPES):
                continue
            if not _has_in_stock(obj):
                continue
            if any(_same_listing(requested_url, final_url, value) for value in _structured_urls(obj, final_url)):
                return True
    return False


def _fotocasa_initial_props_available(requested_url: str, final_url: str, html: str) -> bool:
    # Lazy import keeps the pure semantics module importable without coupling
    # its module initialization to portal parsing.
    from .services_portal_extractors import fotocasa_initial_props_occupancy_is_available
    return fotocasa_initial_props_occupancy_is_available(html, requested_url, final_url)


def _is_fotocasa_host(url: Any) -> bool:
    host = (urlparse(str(url or "")).hostname or "").casefold()
    return host == "fotocasa.es" or host.endswith(".fotocasa.es")


def _fotocasa_detail_listing_id(url: Any) -> str:
    if not _is_fotocasa_host(url):
        return ""
    path = re.sub(r"/+", "/", urlparse(str(url or "")).path).casefold()
    match = re.search(r"^/es/alquiler/(?:[^/]+/)+(?P<listing_id>\d{6,})/d/?$", path)
    return match.group("listing_id") if match else ""


def _is_fotocasa_search_results_url(url: Any) -> bool:
    if not _is_fotocasa_host(url):
        return False
    path = re.sub(r"/+", "/", urlparse(str(url or "")).path).casefold()
    return bool(re.fullmatch(r"/es/alquiler/viviendas(?:/[^/]+)+/l/?", path))


def fotocasa_detail_redirected_to_search_results(
    requested_url: Any, final_url: Any,
) -> bool:
    """Strong negative: a valid Fotocasa detail identity became a known list."""
    requested_id = _fotocasa_detail_listing_id(requested_url)
    if not requested_id or not _is_fotocasa_host(final_url):
        return False
    if _normalized_location(requested_url) == _normalized_location(final_url):
        return False
    if requested_id in _listing_ids(final_url):
        return False
    return _is_fotocasa_search_results_url(final_url)


def classify_listing_availability(
    *, requested_url: str, status_code: Any, final_url: str, html: str,
    error: str = "", explicit_negative: str = "",
) -> dict[str, str]:
    try:
        status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status = None

    if (
        not error and status is not None and 200 <= status < 400
        and fotocasa_detail_redirected_to_search_results(requested_url, final_url)
    ):
        return _evidence(
            UNAVAILABLE_CONFIRMED,
            "detail_redirected_to_search_results_identity_lost",
            "redirect",
        )
    if status in {404, 410}:
        return _evidence(UNAVAILABLE_CONFIRMED, f"http_{status}", "http_status")
    if explicit_negative:
        return _evidence(UNAVAILABLE_CONFIRMED, explicit_negative, "explicit_text")
    if status in {401, 403, 408, 425, 429}:
        return _evidence(AVAILABLE_UNKNOWN, f"http_{status}", "http_status")
    if error:
        signal = "timeout" if "timeout" in error.casefold() or "timed out" in error.casefold() else "network_error"
        return _evidence(AVAILABLE_UNKNOWN, signal, "network")
    if status is None or status >= 500:
        return _evidence(AVAILABLE_UNKNOWN, "http_unknown" if status is None else "http_5xx", "http_status")
    if not (200 <= status < 400):
        return _evidence(AVAILABLE_UNKNOWN, f"http_{status}", "http_status")
    if _strong_structured_listing(requested_url, final_url, html):
        return _evidence(AVAILABLE_CONFIRMED, "structured_listing_in_stock_same_listing", "structured_data")
    if _fotocasa_initial_props_available(requested_url, final_url, html):
        return _evidence(
            AVAILABLE_CONFIRMED,
            "fotocasa_initial_props_occupancy_is_available",
            "fotocasa_initial_props",
        )
    return _evidence(AVAILABLE_UNKNOWN, "no_strong_positive_signal", "unknown")
