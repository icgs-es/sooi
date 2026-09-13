from django import template
from apps.busquedas.price import format_euro_price

register = template.Library()


@register.filter
def euro(value):
    return format_euro_price(value)


@register.filter
def result_confidence_label(classification):
    """Customer-facing confidence; persisted quality classifications stay intact."""
    value = str(classification or "").strip().lower()
    if value in {"verified", "accepted"}:
        return "Verificado"
    if value == "reviewable":
        return "Para revisar"
    if value == "discarded":
        return "Descartado"
    return ""


@register.filter
def result_confidence_kind(classification):
    value = str(classification or "").strip().lower()
    if value in {"verified", "accepted"}:
        return "ok"
    if value == "reviewable":
        return "warn"
    if value == "discarded":
        return "bad"
    return ""


@register.filter
def result_confidence_message(reason):
    """Expose only approved commercial copy, never internal reason details."""
    if "availability_unknown" in str(reason or "").lower():
        return "Disponibilidad pendiente de confirmar"
    if any(marker in str(reason or "").lower() for marker in (
        "availability_unavailable:", "ai_probe_unavailable:",
    )):
        return "No disponible"
    return ""


@register.filter
def result_action_label(action):
    return {
        "would_create_captured": "Listo para captar",
        "would_create_in_review": "Revisión necesaria",
        "hold_for_review": "Pendiente de revisión",
        "skip_discarded": "No apto",
        "skip_duplicate": "Ya existente",
    }.get(str(action or "").strip().lower(), "")
