from __future__ import annotations

import socket
import unicodedata
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.utils import timezone

from apps.inmuebles.models import CapturedProperty


AVAILABLE = "available"
UNAVAILABLE = "unavailable"
NOT_VERIFIABLE = "not_verifiable"
TEMPORARY_ERROR = "temporary_error"


@dataclass
class AvailabilityResult:
    availability: str
    reason: str
    status_code: int | None = None
    final_url: str = ""
    error: str = ""
    evidence: str = ""

    @property
    def is_available(self) -> bool:
        return self.availability == AVAILABLE

    @property
    def is_unavailable(self) -> bool:
        return self.availability == UNAVAILABLE

    @property
    def is_not_verifiable(self) -> bool:
        return self.availability == NOT_VERIFIABLE

    @property
    def is_temporary_error(self) -> bool:
        return self.availability == TEMPORARY_ERROR


def _normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value


def _extract_host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _read_html(response, max_bytes: int = 250_000) -> str:
    raw = response.read(max_bytes)
    return raw.decode("utf-8", errors="ignore")


def fetch_url_probe(url: str, timeout: int = 8) -> tuple[int | None, str, str, str]:
    if not url:
        return None, "", "", "empty_url"

    headers = {
        "User-Agent": "Mozilla/5.0 SOOI/2.1 AvailabilityChecker",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            final_url = response.geturl()
            html = _read_html(response)
            return status_code, final_url, html, ""
    except HTTPError as exc:
        try:
            html = _read_html(exc, max_bytes=120_000)
        except Exception:
            html = ""
        return exc.code, url, html, ""
    except (URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return None, url, "", exc.__class__.__name__


def classify_availability_response(
    *,
    status_code: int | None,
    final_url: str,
    html: str,
    error: str = "",
) -> AvailabilityResult:
    text = _normalize_text(html)
    final_url_norm = _normalize_text(final_url)

    if status_code in {404, 410}:
        return AvailabilityResult(
            availability=UNAVAILABLE,
            reason=f"http {status_code}",
            status_code=status_code,
            final_url=final_url,
            evidence="http_status",
        )

    if status_code in {401, 403, 429}:
        return AvailabilityResult(
            availability=NOT_VERIFIABLE,
            reason=f"http {status_code} no verificable",
            status_code=status_code,
            final_url=final_url,
            evidence="blocked_or_limited",
        )

    if status_code is not None and status_code >= 500:
        return AvailabilityResult(
            availability=TEMPORARY_ERROR,
            reason=f"http {status_code}",
            status_code=status_code,
            final_url=final_url,
            evidence="server_error",
        )

    if error:
        return AvailabilityResult(
            availability=TEMPORARY_ERROR,
            reason=error,
            status_code=status_code,
            final_url=final_url,
            error=error,
            evidence="network_error",
        )

    not_verifiable_markers = [
        "captcha",
        "verifica que eres humano",
        "verificar que eres humano",
        "robot",
        "access denied",
        "forbidden",
        "too many requests",
        "inicia sesion",
        "login obligatorio",
    ]

    if any(marker in text or marker in final_url_norm for marker in not_verifiable_markers):
        return AvailabilityResult(
            availability=NOT_VERIFIABLE,
            reason="bloqueo, captcha o login obligatorio",
            status_code=status_code,
            final_url=final_url,
            evidence="not_verifiable_marker",
        )

    unavailable_markers = [
        "lo sentimos, este anuncio ya no esta publicado",
        "lo sentimos este anuncio ya no esta publicado",
        "anuncio ya no esta publicado",
        "este anuncio ya no esta publicado",
        "ya no esta publicado",
        "el anunciante lo dio de baja",
        "anuncio retirado",
        "no disponible",
    ]

    for marker in unavailable_markers:
        if marker in text:
            return AvailabilityResult(
                availability=UNAVAILABLE,
                reason="anuncio retirado o no publicado",
                status_code=status_code,
                final_url=final_url,
                evidence=marker,
            )

    return AvailabilityResult(
        availability=AVAILABLE,
        reason="sin evidencia de baja",
        status_code=status_code,
        final_url=final_url,
        evidence="ok",
    )


def check_url_availability(url: str, timeout: int = 8) -> AvailabilityResult:
    status_code, final_url, html, error = fetch_url_probe(url, timeout=timeout)
    return classify_availability_response(
        status_code=status_code,
        final_url=final_url,
        html=html,
        error=error,
    )


def check_captured_property_availability(captured_property: CapturedProperty) -> AvailabilityResult:
    return check_url_availability(captured_property.source_url)


def apply_captured_property_cleanup(
    captured_property: CapturedProperty,
    result: AvailabilityResult,
    *,
    commit: bool = False,
) -> str:
    if not result.is_unavailable:
        return "no_action"

    if not commit:
        return "would_discard"

    captured_property.status = CapturedProperty.Status.DISCARDED
    captured_property.discard_reason = f"auto: {result.reason}"[:100]

    update_fields = ["status", "discard_reason"]

    if hasattr(captured_property, "updated_at"):
        captured_property.updated_at = timezone.now()
        update_fields.append("updated_at")

    captured_property.save(update_fields=update_fields)
    return "discarded"
