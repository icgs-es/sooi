from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


@dataclass
class QualityGateResult:
    accepted: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _item_text(item: Any) -> str:
    parts = [
        getattr(item, "title", "") or "",
        getattr(item, "summary", "") or "",
        getattr(item, "source_name", "") or "",
        getattr(item, "source_url", "") or "",
    ]
    return " ".join(parts).lower()


def evaluate_ai_quality_gate(
    *,
    item: Any,
    search_profile: Any,
    normalized_url: str,
    url_is_detail: bool,
) -> QualityGateResult:
    warnings: list[str] = []

    url = (normalized_url or getattr(item, "source_url", "") or "").strip()
    text = _item_text(item)
    parsed = urlparse(url)

    if not url:
        return QualityGateResult(False, "sin_url")

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return QualityGateResult(False, "url_invalida")

    host = parsed.netloc.lower()
    path = parsed.path.lower().strip("/")

    if not path:
        return QualityGateResult(False, "url_home_no_ficha")

    if not url_is_detail:
        return QualityGateResult(False, "url_no_ficha_detalle")

    if "fotocasa" in host and "anuncio no disponible" in text:
        return QualityGateResult(False, "fotocasa_anuncio_no_disponible")

    retired_phrases = [
        "anuncio no disponible",
        "este anuncio ya no está disponible",
        "este anuncio ya no esta disponible",
        "anuncio retirado",
        "publicación no disponible",
        "publicacion no disponible",
        "publicación eliminada",
        "publicacion eliminada",
        "página no encontrada",
        "pagina no encontrada",
        "no encontramos el anuncio",
        "ha sido eliminado",
    ]

    if any(phrase in text for phrase in retired_phrases):
        return QualityGateResult(False, "anuncio_retirado_o_no_disponible")

    price = _as_decimal(getattr(item, "price", None))
    min_price = _as_decimal(getattr(search_profile, "min_price", None))
    max_price = _as_decimal(getattr(search_profile, "max_price", None))

    if price is None:
        warnings.append("precio_no_verificado_desde_portal")
    else:
        if min_price is not None and price < min_price:
            return QualityGateResult(False, "precio_por_debajo_del_filtro")
        if max_price is not None and price > max_price:
            return QualityGateResult(False, "precio_por_encima_del_filtro")

    if "precio no verificado" in text or "precio aproximado" in text:
        warnings.append("precio_no_verificado_desde_portal")

    return QualityGateResult(True, warnings=warnings)


def quality_gate_warning_message(item: Any, gate: QualityGateResult) -> str:
    title = (getattr(item, "title", "") or "sin título").strip()
    url = (getattr(item, "source_url", "") or "sin url").strip()
    return f"QUALITY_GATE_DISCARD reason={gate.reason} title={title[:120]} url={url[:300]}"
