"""Read-only presentation helpers for the proactive Daily Digest preview."""

from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .daily_workflow import build_daily_workflow


DAILY_DIGEST_SUBJECT = "SOOI · Tu trabajo de hoy"

COUNTER_LABELS = {
    "captures_ready": "Captaciones disponibles pendientes",
    "captures_availability": "Captaciones sin verificar",
    "opportunities_overdue": "Seguimientos vencidos",
    "opportunities_today": "Seguimientos para hoy",
    "opportunities_no_next": "Oportunidades sin próxima revisión",
    "tasks_overdue": "Tareas vencidas",
    "tasks_today": "Tareas para hoy",
}


def _url(path, base_url):
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"


def _location(obj):
    municipality = getattr(obj, "municipality", "")
    province = getattr(obj, "province", "")
    if not municipality and not province:
        captured = getattr(obj, "captured_property", None)
        municipality = getattr(captured, "municipality", "")
        province = getattr(captured, "province", "")
    return " · ".join(value for value in (municipality, province) if value)


def _description(kind, obj):
    if kind == "opportunity":
        action = obj.get_next_action_type_display()
        notes = getattr(obj, "next_action_notes", "")
        return " · ".join(value for value in (action, notes) if value)
    if kind == "task":
        return getattr(obj, "description", "")
    return "Revisar la captación y completar la decisión comercial pendiente."


def _item_url(kind, pk):
    route = {
        "capture": "capturedproperty_detail",
        "opportunity": "opportunity_detail",
        "task": "task_detail",
    }[kind]
    return reverse(route, kwargs={"pk": pk})


def _present_item(item, base_url):
    obj = item["object"]
    priority = ""
    if item.get("priority") and hasattr(obj, "get_priority_display"):
        priority = obj.get_priority_display()
    return {
        "kind": item["kind"],
        "category": item["category"],
        "reason": item["reason_label"],
        "title": obj.title,
        "location": _location(obj),
        "description": _description(item["kind"], obj),
        "due_at": item.get("due_at"),
        "priority": priority,
        "action_label": {
            "capture": "Abrir captación",
            "opportunity": "Abrir oportunidad",
            "task": "Abrir tarea",
        }[item["kind"]],
        "url": _url(_item_url(item["kind"], obj.pk), base_url),
    }


def build_daily_digest(user, *, now=None, limit=5, base_url=None):
    """Build one transport-independent digest from the canonical daily selector."""
    workflow = build_daily_workflow(user, now=now, limit=limit)
    generated_at = timezone.localtime(workflow["generated_at"])
    items = [_present_item(item, base_url) for item in workflow["attention"]]
    total_attention = workflow["attention_total"]
    return {
        "subject": DAILY_DIGEST_SUBJECT,
        "owner": user,
        "recipient_email": (user.email or "").strip(),
        "generated_at": generated_at,
        "local_date": generated_at.date(),
        "total_attention": total_attention,
        "counters": workflow["counters"],
        "counter_items": [
            {"key": key, "label": COUNTER_LABELS[key], "value": value}
            for key, value in workflow["counters"].items()
            if value
        ],
        "items": items,
        "overflow_count": max(total_attention - len(items), 0),
        "is_all_clear": workflow["is_all_clear"],
        "today_url": _url(reverse("dashboard"), base_url),
    }


def render_daily_digest_text(digest):
    return render_to_string("core/emails/daily_digest.txt", {"digest": digest})


def render_daily_digest_html(digest):
    return render_to_string("core/emails/daily_digest.html", {"digest": digest})


def render_daily_digest(user, *, now=None, limit=5, base_url=None):
    digest = build_daily_digest(user, now=now, limit=limit, base_url=base_url)
    return {
        "subject": digest["subject"],
        "text_body": render_daily_digest_text(digest),
        "html_body": render_daily_digest_html(digest),
        "digest": digest,
    }
