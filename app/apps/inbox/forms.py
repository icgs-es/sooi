from decimal import Decimal
from urllib.parse import urlparse, urlunparse
from django import forms

from .models import EmailAccount


import re
class EmailAccountForm(forms.ModelForm):
    class Meta:
        model = EmailAccount
        fields = [
            "name",
            "email_address",
            "provider_label",
            "imap_host",
            "imap_port",
            "imap_use_ssl",
            "imap_username",
            "imap_password",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ej. Captaciones Gmail"}),
            "email_address": forms.EmailInput(attrs={"placeholder": "correo@empresa.com"}),
            "provider_label": forms.TextInput(attrs={"placeholder": "Ej. Gmail, IONOS, Outlook"}),
            "imap_host": forms.TextInput(attrs={"placeholder": "Ej. imap.gmail.com"}),
            "imap_username": forms.TextInput(attrs={"placeholder": "Normalmente el email completo"}),
            "imap_password": forms.PasswordInput(render_value=True),
        }


from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.busquedas.models import SearchProfile
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty


ACTIVE_SEARCH_STATUSES = [
    SearchProfile.Status.ACTIVE,
    SearchProfile.Status.PAUSED,
]


def _guess_source_from_url(source_url: str):
    if not source_url:
        return None

    host = urlparse(source_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    domain_map = [
        ("idealista.", ["idealista"]),
        ("fotocasa.", ["fotocasa"]),
        ("habitaclia.", ["habitaclia"]),
        ("pisos.com", ["pisos"]),
        ("servihabitat.", ["servihabitat"]),
        ("solvia.", ["solvia"]),
        ("altamirainmuebles.", ["altamira"]),
        ("terrenos.", ["terrenos"]),
        ("yaencontre.", ["yaencontre"]),
    ]

    keywords = []
    for domain_fragment, source_keywords in domain_map:
        if domain_fragment in host:
            keywords = source_keywords
            break

    if not keywords:
        return None

    qs = Source.objects.all()

    for keyword in keywords:
        source = (
            qs.filter(code__icontains=keyword).first()
            or qs.filter(name__icontains=keyword).first()
            or qs.filter(base_url__icontains=keyword).first()
        )
        if source:
            return source

    return None



def _canonical_property_detail_url(url: str) -> str:
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        scheme = parsed.scheme or "https"
        host = parsed.netloc.lower()
        path = parsed.path or ""

        if host.startswith("www."):
            host = host[4:]

        canonical_path = ""

        idealista_match = re.search(r"/inmueble/(\d+)/?", path)
        if "idealista." in host and idealista_match:
            canonical_path = f"/inmueble/{idealista_match.group(1)}/"

        elif "habitaclia." in host and re.search(r"-i\d+\.htm$", path):
            canonical_path = path

        elif "fotocasa." in host and re.search(r"/\d+/d/?$", path):
            canonical_path = path

        elif "pisos.com" in host and re.search(r"/(alquilar|comprar)/", path) and re.search(r"\d+", path):
            canonical_path = path

        elif "servihabitat." in host and re.search(r"/(venta|alquiler)/", path):
            canonical_path = path

        elif ("solvia." in host or "altamirainmuebles." in host or "terrenos." in host) and path.strip("/"):
            canonical_path = path

        if not canonical_path:
            return ""

        return urlunparse((scheme, host, canonical_path.rstrip("/") + "/", "", "", ""))
    except Exception:
        return ""


def _select_best_property_url(urls: list[str]) -> str:
    for url in urls or []:
        canonical = _canonical_property_detail_url(url)
        if canonical:
            return canonical
    return ""


def _parse_decimal_from_match(value: str):
    if not value:
        return None

    cleaned = value.replace(".", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _extract_price(text: str):
    rent_match = re.search(r"(\d[\d\.\s]*)(?:,\d+)?\s*€\s*/\s*mes", text, re.IGNORECASE)
    if rent_match:
        return _parse_decimal_from_match(rent_match.group(1))

    sale_match = re.search(r"(\d[\d\.\s]*)(?:,\d+)?\s*€", text, re.IGNORECASE)
    if sale_match:
        return _parse_decimal_from_match(sale_match.group(1))

    return None


def _extract_first_decimal(pattern: str, text: str):
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return _parse_decimal_from_match(match.group(1))


def _extract_first_int(pattern: str, text: str):
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _guess_operation_type_from_text(text: str) -> str:
    lowered = text.lower()

    if "€/mes" in lowered or "alquiler" in lowered or "se alquila" in lowered or "_rent_" in lowered:
        return CapturedProperty.OperationType.RENT

    return CapturedProperty.OperationType.SALE


def _guess_property_type_from_text(text: str) -> str:
    lowered = text.lower()

    if "terreno" in lowered or "solar" in lowered or "parcela" in lowered:
        return CapturedProperty.PropertyType.LAND

    if "local" in lowered or "comercial" in lowered:
        return CapturedProperty.PropertyType.COMMERCIAL

    if "casa" in lowered or "chalet" in lowered or "finca" in lowered:
        return CapturedProperty.PropertyType.HOUSE

    if "piso" in lowered or "apartamento" in lowered or "ático" in lowered or "atico" in lowered:
        return CapturedProperty.PropertyType.FLAT

    return CapturedProperty.PropertyType.FLAT



def _clean_listing_text(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+\d[\d\.\s]*(?:,\d+)?\s*€\s*/\s*mes\.?$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+\d[\d\.\s]*(?:,\d+)?\s*€\.?$", "", value, flags=re.IGNORECASE)
    return value.strip(" .,-")



def _build_compact_title_from_email_text(text: str) -> str:
    lowered = (text or "").lower()

    if "alquiler de piso" in lowered or "alquiler piso" in lowered:
        base = "Alquiler de piso"
    elif "venta de piso" in lowered or "piso en venta" in lowered:
        base = "Venta de piso"
    elif "alquiler de casa" in lowered or "alquiler casa" in lowered:
        base = "Alquiler de casa"
    elif "venta de casa" in lowered or "casa en venta" in lowered:
        base = "Venta de casa"
    else:
        return ""

    price = _extract_price(text)
    area = _extract_first_decimal(r"(\d+(?:[,.]\d+)?)\s*m²", text)
    bedrooms = _extract_first_int(r"(\d+)\s*(?:hab|habitacion|habitaciones)", text)

    parts = [base]

    if price is not None:
        if "€/mes" in lowered or "alquiler" in lowered:
            parts.append(f"{int(price)} €/mes")
        else:
            parts.append(f"{int(price)} €")

    if area is not None:
        parts.append(f"{int(area)} m²")

    if bedrooms is not None:
        parts.append(f"{bedrooms} hab.")

    return " · ".join(parts)


def _extract_listing_title(text: str, fallback: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]

    for line in lines:
        if len(line) > 180:
            continue

        lowered = line.lower()
        if lowered.startswith(("piso en ", "apartamento en ", "ático en ", "atico en ", "casa en ", "chalet en ", "terreno en ", "local en ")):
            return _clean_listing_text(line)

    compact = re.sub(r"\s+", " ", text or "")
    match = re.search(
        r"((?:Piso|Apartamento|Ático|Atico|Casa|Chalet|Terreno|Local)\s+en\s+.{8,170}?)(?:\s+\d[\d\.\s]*\s*€)",
        compact,
        re.IGNORECASE,
    )
    if match:
        return _clean_listing_text(match.group(1))

    compact_title = _build_compact_title_from_email_text(text)
    if compact_title:
        return compact_title

    if fallback and "quiere que veas este inmueble" not in fallback.lower():
        return fallback

    return compact_title or "Captación recibida por email"


def _extract_municipality_from_title(title: str) -> str:
    cleaned_title = _clean_listing_text(title)
    parts = [part.strip() for part in cleaned_title.split(",") if part.strip()]
    if len(parts) >= 2:
        return _clean_listing_text(parts[-1])
    return ""


def _extract_initial_capture_data(inbound_email):
    body = inbound_email.body_text or ""
    subject = inbound_email.subject or ""
    full_text = f"{subject}\n{body}"

    selected_url = _select_best_property_url(inbound_email.detected_urls or [])
    title = _extract_listing_title(body, subject)
    price = _extract_price(full_text)
    area_m2 = _extract_first_decimal(r"(\d+(?:[,.]\d+)?)\s*m²", full_text)
    bedrooms = _extract_first_int(r"(\d+)\s*hab", full_text)
    bathrooms = _extract_first_int(r"(\d+)\s*bañ", full_text)

    description = body[:1200] if body else inbound_email.snippet

    return {
        "title": title,
        "source_url": selected_url,
        "operation_type": _guess_operation_type_from_text(full_text),
        "property_type": _guess_property_type_from_text(full_text),
        "price": price,
        "area_m2": area_m2,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "municipality": _extract_municipality_from_title(title),
        "zone_text": title,
        "description_raw": description,
        "manual_notes": f"Captación creada desde email #{inbound_email.pk}. Remitente: {inbound_email.from_email or '—'}",
    }



class InboundEmailConvertToCaptureForm(forms.Form):
    search_profile = forms.ModelChoiceField(
        label="Búsqueda asociada",
        queryset=SearchProfile.objects.none(),
        required=True,
    )
    source = forms.ModelChoiceField(
        label="Fuente real",
        queryset=Source.objects.none(),
        required=True,
    )
    operation_type = forms.ChoiceField(
        label="Operación",
        choices=CapturedProperty.OperationType.choices,
        required=True,
    )
    property_type = forms.ChoiceField(
        label="Tipología",
        choices=CapturedProperty.PropertyType.choices,
        required=True,
    )
    title = forms.CharField(
        label="Título",
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Ej. Piso recibido por email"}),
    )
    source_url = forms.URLField(
        label="URL origen",
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://..."}),
    )
    price = forms.DecimalField(
        label="Precio",
        required=False,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    province = forms.CharField(label="Provincia", max_length=100, required=False)
    municipality = forms.CharField(label="Municipio", max_length=100, required=False)
    zone_text = forms.CharField(label="Zona / referencia", max_length=180, required=False)
    bedrooms = forms.IntegerField(label="Dormitorios", required=False, min_value=0)
    bathrooms = forms.IntegerField(label="Baños", required=False, min_value=0)
    area_m2 = forms.DecimalField(
        label="Superficie m²",
        required=False,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    description_raw = forms.CharField(
        label="Descripción operativa",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    manual_notes = forms.CharField(
        label="Notas internas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, user=None, inbound_email=None, **kwargs):
        self.user = user
        self.inbound_email = inbound_email
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["search_profile"].queryset = (
                SearchProfile.objects
                .filter(owner=user)
                .filter(status__in=ACTIVE_SEARCH_STATUSES)
                .order_by("status", "name")
            )

        self.fields["source"].queryset = (
            Source.objects
            .exclude(code__in=["exploracion-ia", "manual"])
            .order_by("name")
        )

        self.fields["search_profile"].empty_label = "Selecciona búsqueda asociada"
        self.fields["source"].empty_label = "Selecciona fuente real"

        if inbound_email is not None and not self.is_bound:
            initial_data = _extract_initial_capture_data(inbound_email)
            guessed_source = _guess_source_from_url(initial_data.get("source_url", ""))

            if guessed_source:
                initial_data["source"] = guessed_source.pk

            self.initial.update(initial_data)

    def clean_search_profile(self):
        search_profile = self.cleaned_data.get("search_profile")

        if not search_profile:
            raise ValidationError("Debes asociar la captación a una búsqueda activa.")

        if self.user is not None and search_profile.owner_id != self.user.id:
            raise ValidationError("La búsqueda seleccionada no pertenece a tu usuario.")

        return search_profile
