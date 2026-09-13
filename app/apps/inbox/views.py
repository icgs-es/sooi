from django.contrib import messages
import json
from django.core.management import call_command
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.busquedas.models import SearchProfile
from apps.inmuebles.models import CapturedProperty

from .forms import EmailAccountForm, InboundEmailConvertToCaptureForm
from .models import EmailAccount, InboundEmail
from apps.core.observability import emit


@login_required
def inbox_sync(request):
    accounts = EmailAccount.objects.filter(
        owner=request.user,
        is_active=True,
        email_address__iexact="captaciones@sooi.io",
    )

    # Fallback prudente por si en algún entorno cambia el email técnico.
    if not accounts.exists():
        accounts = EmailAccount.objects.filter(owner=request.user, is_active=True)

    if not accounts.exists():
        messages.error(request, "No hay buzón técnico activo para sincronizar.")
        return redirect("inbox_list")

    results = []
    for account in accounts:
        results.append(json.loads(call_command(
            "sync_inbox_email",
            account_id=account.id,
            limit=50,
            update_existing=True,
        )))

    states = {result["status"] for result in results}
    if states == {"success"}:
        state, notice = "success", "Bandeja actualizada correctamente."
        messages.success(request, notice)
    elif "success" in states or "partial" in states:
        state, notice = "partial", "Bandeja actualizada parcialmente; consulte el estado técnico."
        messages.warning(request, notice)
    elif "retry" in states and "failure" not in states:
        state, notice = "retry", "Proveedor temporalmente no disponible; reintento requerido."
        messages.warning(request, notice)
    else:
        state, notice = "failure", "No se pudo actualizar la bandeja."
        messages.error(request, notice)
    emit("inbox_sync", state, getattr(request, "correlation_id", None),
         component="imap", operation="sync", owner_id=request.user.pk, count=len(results))
    return redirect("inbox_list")


@login_required
def inbox_list(request):
    status = request.GET.get("status", "").strip()
    search_profile_id = request.GET.get("search_profile_id", "").strip()

    qs = (
        InboundEmail.objects
        .select_related("account", "search_profile", "captured_property")
        .filter(owner=request.user)
        .order_by("-received_at", "-created_at")
    )


    if status == "all":
        pass
    elif status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status=InboundEmail.Status.NEW)

    if search_profile_id:
        qs = qs.filter(search_profile_id=search_profile_id)

    available_search_profiles = SearchProfile.objects.filter(owner=request.user).order_by("status", "name")

    return render(
        request,
        "inbox/inbox_list.html",
        {
            "items": qs,
            "current_status": status,
            "current_search_profile_id": search_profile_id,
            "status_choices": InboundEmail.Status.choices,
            "available_search_profiles": available_search_profiles,
        },
    )



# === SOOI V2.3 · EMAIL -> CAPTACION HELPERS ===
def _sooi_email_clean_text(value):
    import html
    import re

    text = html.unescape(str(value or ""))
    replacements = {
        "Ã¡": "á", "Ã©": "é", "Ã­": "í", "Ã³": "ó", "Ãº": "ú",
        "Ã�": "Í", "Ã±": "ñ", "Ã‘": "Ñ",
        "Ã�": "Á", "Ã‰": "É", "Ã“": "Ó", "Ãš": "Ú",
        "Â¿": "¿", "Â¡": "¡", "Âº": "º", "Âª": "ª", "Â·": "·",
        "â‚¬": "€",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sooi_email_text_blob(item):
    return _sooi_email_clean_text(
        " ".join([
            getattr(item, "subject", "") or "",
            getattr(item, "snippet", "") or "",
            getattr(item, "body_text", "") or "",
            getattr(item, "from_email", "") or "",
            getattr(item, "from_name", "") or "",
        ])
    )


def _sooi_email_trim(value, limit):
    value = _sooi_email_clean_text(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _sooi_email_clean_url(url):
    from urllib.parse import urlparse, urlunparse

    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""

    parsed = urlparse(url)

    if not parsed.netloc:
        return ""

    # Quitamos tracking. En fichas inmobiliarias no hace falta conservar query.
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _sooi_email_is_property_url(url):
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    reject_hosts = [
        "static.", "static-", "images.", "img.", "media.", "facebook.",
        "instagram.", "twitter.", "x.com", "youtube.", "linkedin.",
        "tiktok.", "google.", "apple.", "play.google", "w3.org",
        "hipotecas.", "sgt.email.", "email.habitaclia.",
        "apicondor.", "avast.", "ionos.",
    ]

    if any(token in host for token in reject_hosts):
        return False

    reject_paths = [
        "/legal", "/privacy", "/politica", "/baja", "/unsubscribe",
        "/sobre/", "/usuario/", "/miyaencontre/", "/hab_cliente/",
        "/hab_usuarios/", "/hab_inmuebles/landing", "/info/contacto",
        "/ayuda/", "/app", "/download",
    ]

    if any(token in path for token in reject_paths):
        return False

    if "habitaclia.com" in host:
        return "inmueble_" in path and any(x in path for x in ["/alquiler-", "/comprar-", "/venta-"])

    if "fotocasa.es" in host:
        return "/es/alquiler/" in path or "/es/comprar/" in path or "/es/venta/" in path or path.endswith("/d")

    if "idealista.com" in host:
        return "/inmueble/" in path

    if "yaencontre.com" in host:
        return "inmueble-" in path and ("/alquiler/" in path or "/venta/" in path)

    if "pisos.com" in host:
        return "/alquiler/" in path or "/comprar/" in path or "/venta/" in path

    if "servihabitat.com" in host:
        return "/venta/" in path or "/alquiler/" in path

    if "solvia.es" in host or "altamirainmuebles.com" in host:
        return True

    return False


def _sooi_email_best_property_url(item):
    urls = getattr(item, "detected_urls", None) or []

    for url in urls:
        clean = _sooi_email_clean_url(url)
        if clean and _sooi_email_is_property_url(clean):
            return clean

    return ""


def _sooi_email_guess_source(item, source_url=""):
    from apps.fuentes.models import Source

    blob = _sooi_email_text_blob(item).lower()
    url = (source_url or _sooi_email_best_property_url(item) or "").lower()
    from_email = (getattr(item, "from_email", "") or "").lower()

    probe = " ".join([blob, url, from_email])

    mapping = [
        ("habitaclia", ["habitaclia.com", "email.habitaclia.com", "habitaclia"]),
        ("fotocasa", ["fotocasa.es", "fotocasa"]),
        ("idealista", ["idealista.com", "idealista"]),
        ("yaencontre", ["yaencontre.com", "yaencontre"]),
        ("pisos", ["pisos.com"]),
        ("servihabitat", ["servihabitat.com", "servihabitat"]),
        ("solvia", ["solvia.es", "solvia"]),
        ("altamira", ["altamirainmuebles.com", "altamira"]),
    ]

    for code, tokens in mapping:
        if any(token in probe for token in tokens):
            src = Source.objects.filter(code=code).first()
            if src:
                return src

    return Source.objects.filter(code="email").first() or Source.objects.exclude(code__in=["manual", "exploracion-ia"]).first()


def _sooi_email_extract_price(item):
    import re
    from decimal import Decimal, InvalidOperation

    text = _sooi_email_text_blob(item)

    pattern = r"(\d{1,3}(?:[.\s]\d{3})*|\d{2,7})(?:,\d{1,2})?\s*(?:€|eur|euros)"
    match = re.search(pattern, text, flags=re.I)

    if not match:
        return None

    raw = match.group(1).replace(".", "").replace(" ", "").replace(",", ".")

    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _sooi_email_extract_int(patterns, text):
    import re

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    number_words = {
        "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
        "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    }

    for word, value in number_words.items():
        if re.search(rf"\b{word}\s+(?:habitaciones|dormitorios|habs?)\b", text, flags=re.I):
            return value

    return None


def _sooi_email_extract_area(item):
    import re
    from decimal import Decimal

    text = _sooi_email_text_blob(item)

    match = re.search(r"(\d{2,4})\s*m\s*(?:2|²)", text, flags=re.I)
    if not match:
        return None

    return Decimal(match.group(1))


def _sooi_email_infer_property_type(item):
    from apps.inmuebles.models import CapturedProperty

    text = _sooi_email_text_blob(item).lower()

    if any(token in text for token in ["chalet", "casa", "adosada", "pareada", "vivienda unifamiliar"]):
        return CapturedProperty.PropertyType.HOUSE

    if "local" in text or "comercial" in text:
        return CapturedProperty.PropertyType.COMMERCIAL

    if "terreno" in text or "parcela" in text or "solar" in text:
        return CapturedProperty.PropertyType.LAND

    return CapturedProperty.PropertyType.FLAT


def _sooi_email_infer_operation(item, search_profile=None):
    from apps.inmuebles.models import CapturedProperty

    if search_profile is not None:
        return search_profile.operation_type

    text = _sooi_email_text_blob(item).lower()

    if any(token in text for token in ["alquiler", "alquila", "€/mes", "eur/mes"]):
        return CapturedProperty.OperationType.RENT

    return CapturedProperty.OperationType.SALE


def _sooi_email_infer_municipality(item, search_profile=None):
    import re

    text = _sooi_email_text_blob(item)

    if search_profile is not None:
        zone = _sooi_email_clean_text(getattr(search_profile, "zone", "") or "")
        for part in [p.strip() for p in zone.split(",") if p.strip()]:
            if part and part.lower() in text.lower():
                return _sooi_email_trim(part, 120)

    match = re.search(
        r"\b(?:piso|casa|chalet|apartamento|vivienda)\s+en\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s\-]{2,50})",
        text,
        flags=re.I,
    )

    if match:
        value = match.group(1)
        value = re.split(r"\s+(?:las|los|la|el|zona|calle|avenida|av\.|piso|casa)\b", value, maxsplit=1, flags=re.I)[0]
        return _sooi_email_trim(value, 120)

    return ""


def _sooi_email_infer_zone(item, municipality=""):
    import re

    text = _sooi_email_text_blob(item)
    parts = []

    if municipality:
        pattern = rf"\ben\s+{re.escape(municipality)}\s+(.{{3,120}}?)\s+(?:piso|casa|chalet|apartamento|vivienda|\d+\s*m|{chr(8364)}|eur)"
        match = re.search(pattern, text, flags=re.I)
        if match:
            zone = match.group(1)
            zone = re.sub(r"\s*-\s*$", "", zone).strip(" .,-")
            if zone:
                parts.append(zone)

    match = re.search(r"\bzona\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s\.]{3,80})", text, flags=re.I)
    if match:
        zone = match.group(1)
        zone = re.split(r"\s+\d{2,4}\s*m|\.\s|,|\|", zone, maxsplit=1)[0]
        zone = zone.strip(" .,-")
        if zone and zone.lower() not in " ".join(parts).lower():
            parts.append(zone)

    value = " · ".join(parts)

    if not value:
        value = text

    return _sooi_email_trim(value, 180)


def _sooi_email_external_id(source_url, item):
    import hashlib

    base = _sooi_email_clean_url(source_url) or getattr(item, "message_id", "") or f"email-{item.pk}"
    digest = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return _sooi_email_trim(f"email-{item.pk}-{digest}", 180)


def _sooi_email_capture_initial(item):
    search_profile = getattr(item, "search_profile", None)
    source_url = _sooi_email_best_property_url(item)
    source = _sooi_email_guess_source(item, source_url)
    text = _sooi_email_text_blob(item)

    municipality = _sooi_email_infer_municipality(item, search_profile)
    province = getattr(search_profile, "province", "") if search_profile is not None else ""

    title = _sooi_email_clean_text(getattr(item, "subject", "") or "")
    if not title:
        title = text[:180]

    bedrooms = _sooi_email_extract_int([
        r"(\d+)\s*(?:hab\.?|habitaciones|dormitorios)",
    ], text)

    bathrooms = _sooi_email_extract_int([
        r"(\d+)\s*(?:baño|baños)",
    ], text)

    return {
        "search_profile": search_profile,
        "source": source,
        "operation_type": _sooi_email_infer_operation(item, search_profile),
        "property_type": _sooi_email_infer_property_type(item),
        "title": _sooi_email_trim(title, 220),
        "source_url": source_url,
        "price": _sooi_email_extract_price(item),
        "province": province or "",
        "municipality": municipality,
        "zone_text": _sooi_email_infer_zone(item, municipality),
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "area_m2": _sooi_email_extract_area(item),
        "description_raw": _sooi_email_trim(getattr(item, "body_text", "") or getattr(item, "snippet", "") or "", 4000),
        "manual_notes": _sooi_email_trim(
            f"Captación creada desde email #{item.pk}. Remitente: {getattr(item, 'from_email', '')}. Parser: SOOI V2.3 email.",
            1000,
        ),
    }


def _sooi_email_post_value(request, key, fallback=None):
    value = request.POST.get(key, None)
    if value is None or str(value).strip() == "":
        return fallback
    return value


def _sooi_email_decimal_or_none(value):
    from decimal import Decimal, InvalidOperation

    if value in [None, ""]:
        return None

    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _sooi_email_int_or_none(value):
    if value in [None, ""]:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _sooi_email_create_or_update_capture_from_post(request, item):
    from django.core.exceptions import PermissionDenied
    from django.db import transaction

    from apps.busquedas.models import SearchProfile
    from apps.fuentes.models import Source
    from apps.inmuebles.models import CapturedProperty

    if item.owner_id != request.user.id or item.account.owner_id != request.user.id:
        raise PermissionDenied("El mensaje y la cuenta deben pertenecer al usuario.")

    initial = _sooi_email_capture_initial(item)

    search_profile = initial["search_profile"]
    sp_id = request.POST.get("search_profile")

    if sp_id:
        try:
            search_profile = SearchProfile.objects.get(pk=sp_id, owner=request.user)
        except SearchProfile.DoesNotExist:
            raise PermissionDenied("La búsqueda seleccionada no pertenece al usuario.")

    source = initial["source"]
    source_id = request.POST.get("source")

    if source_id:
        source = Source.objects.filter(pk=source_id).first() or source

    source_url = _sooi_email_clean_url(_sooi_email_post_value(request, "source_url", initial["source_url"]))
    if not source_url:
        source_url = initial["source_url"]

    if source is None:
        source = _sooi_email_guess_source(item, source_url)

    operation_type = _sooi_email_infer_operation(item, search_profile)

    data = {
        "owner": request.user,
        "search_profile": search_profile,
        "source": source,
        "entry_mode": CapturedProperty.EntryMode.EMAIL,
        "operation_type": operation_type,
        "property_type": _sooi_email_post_value(request, "property_type", initial["property_type"]),
        "title": _sooi_email_trim(_sooi_email_post_value(request, "title", initial["title"]), 220),
        "source_url": source_url or "",
        "source_external_id": _sooi_email_external_id(source_url, item),
        "price": _sooi_email_decimal_or_none(_sooi_email_post_value(request, "price", initial["price"])),
        "province": _sooi_email_trim(_sooi_email_post_value(request, "province", initial["province"]), 100),
        "municipality": _sooi_email_trim(_sooi_email_post_value(request, "municipality", initial["municipality"]), 120),
        "zone_text": _sooi_email_trim(_sooi_email_post_value(request, "zone_text", initial["zone_text"]), 180),
        "bedrooms": _sooi_email_int_or_none(_sooi_email_post_value(request, "bedrooms", initial["bedrooms"])),
        "bathrooms": _sooi_email_int_or_none(_sooi_email_post_value(request, "bathrooms", initial["bathrooms"])),
        "area_m2": _sooi_email_decimal_or_none(_sooi_email_post_value(request, "area_m2", initial["area_m2"])),
        "description_raw": _sooi_email_trim(_sooi_email_post_value(request, "description_raw", initial["description_raw"]), 4000),
        "manual_notes": _sooi_email_trim(_sooi_email_post_value(request, "manual_notes", initial["manual_notes"]), 1000),
        "status": CapturedProperty.Status.CAPTURED,
        "review_status": CapturedProperty.ReviewStatus.PENDING,
        "ai_signals": [
            {
                "type": "email_parser",
                "version": "sooi_v2_3",
                "email_id": item.pk,
                "source_url_detected": source_url or "",
                "source_detected": source.code if source else "",
                "operation_from_search_profile": bool(search_profile),
            }
        ],
    }

    # Seguridad operativa: si el formulario trae municipio/provincia vacíos,
    # respetamos el expediente asociado.
    if search_profile is not None:
        if not data["province"]:
            data["province"] = search_profile.province or ""
        if not data["operation_type"]:
            data["operation_type"] = search_profile.operation_type

    lookup = {
        "owner": request.user,
        "source": source,
        "source_external_id": data["source_external_id"],
    }

    with transaction.atomic():
        item = (
        InboundEmail.objects
        .select_for_update(of=("self",))
        .select_related("account", "captured_property")
        .get(pk=item.pk)
    )
        if item.owner_id != request.user.id or item.account.owner_id != request.user.id:
            raise PermissionDenied("El mensaje y la cuenta deben pertenecer al usuario.")
        if item.captured_property_id:
            if item.captured_property.owner_id != request.user.id:
                raise PermissionDenied("La captación vinculada pertenece a otro usuario.")
            return item.captured_property

        capture = CapturedProperty.objects.filter(**lookup).first()

        if capture is None:
            capture = CapturedProperty.objects.create(**data)
        else:
            for field, value in data.items():
                setattr(capture, field, value)
            capture.save()

        item.search_profile = search_profile
        item.captured_property = capture
        item.status = item.Status.CONVERTED
        item.save(update_fields=["search_profile", "captured_property", "status", "updated_at"])

    return capture
# === /SOOI V2.3 · EMAIL -> CAPTACION HELPERS ===


@login_required
def inbox_detail(request, pk):
    from django.shortcuts import render
    from apps.inmuebles.forms import CapturedPropertyManualForm

    item = get_object_or_404(InboundEmail, pk=pk, owner=request.user)

    initial = _sooi_email_capture_initial(item)
    convert_form = CapturedPropertyManualForm(user=request.user, initial=initial)

    return render(
        request,
        "inbox/inbox_detail.html",
        {
            "item": item,
            "convert_form": convert_form,
            "email_capture_initial": initial,
            "detected_property_url": initial.get("source_url", ""),
        },
    )



@login_required
def email_account_list_create(request):
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "La configuración de buzones es interna de SOOI.")
        return redirect("inbox_list")

    items = EmailAccount.objects.filter(owner=request.user).order_by("name")

    if request.method == "POST":
        form = EmailAccountForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Cuenta de email creada: "{obj.name}".')
            return redirect("email_account_list")
    else:
        form = EmailAccountForm()

    return render(
        request,
        "inbox/email_account_list.html",
        {
            "items": items,
            "form": form,
        },
    )


@login_required
def inbox_convert_to_capture(request, pk):
    item = get_object_or_404(InboundEmail, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("inbox_detail", pk=item.pk)

    capture = _sooi_email_create_or_update_capture_from_post(request, item)

    emit("inbox_conversion", "success", getattr(request, "correlation_id", None),
         component="inbox", operation="convert", object_type="inbound_email",
         object_id=item.pk, owner_id=request.user.pk)

    messages.success(request, f'Captación creada desde email: "{capture.title}".')
    return redirect("capturedproperty_detail", pk=capture.pk)



@login_required
def inbox_discard(request, pk):
    item = get_object_or_404(InboundEmail, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("inbox_detail", pk=item.pk)

    if item.status != InboundEmail.Status.CONVERTED:
        item.status = InboundEmail.Status.DISCARDED
        item.save(update_fields=["status", "updated_at"])
        messages.success(request, "Email descartado correctamente.")
        emit("inbox_discard", "success", getattr(request, "correlation_id", None),
             component="inbox", operation="discard", object_type="inbound_email",
             object_id=item.pk, owner_id=request.user.pk)
    else:
        messages.info(request, "Este email ya está convertido en captación y no se descarta.")

    return redirect("inbox_list")
