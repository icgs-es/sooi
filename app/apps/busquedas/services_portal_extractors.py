from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .price import normalize_euro_price
from .provider_query_provenance import query_provenance


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SOOI/2.6"
)

WITHDRAWN_PATTERNS = [
    "lo sentimos, este anuncio ya no está publicado",
    "lo sentimos, este anuncio ya no esta publicado",
    "este anuncio ya no está publicado",
    "este anuncio ya no esta publicado",
    "lo dio de baja",
    "dado de baja",
    "anuncio no disponible",
    "inmueble no disponible",
    "publicación no disponible",
    "publicacion no disponible",
]


@dataclass
class PortalCandidate:
    portal: str
    source_provider: str
    title: str
    source_url: str
    price: Optional[int] = None
    bedrooms: Optional[int] = None
    area_m2: Optional[int] = None
    property_type: Optional[str] = None
    municipality: Optional[str] = None
    municipality_match: bool = False
    evidence: Optional[str] = None
    candidate_evidence: Optional[Dict[str, str]] = None


@dataclass
class _HtmlNode:
    tag: str
    attrs: Dict[str, str]
    parent: Optional["_HtmlNode"] = None
    children: Optional[List["_HtmlNode"]] = None
    text: Optional[List[str]] = None

    def __post_init__(self):
        self.children = [] if self.children is None else self.children
        self.text = [] if self.text is None else self.text


class _LocalCardParser(HTMLParser):
    """Small stdlib DOM sufficient to keep listing evidence card-local."""

    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _HtmlNode(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag):
        wanted = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].text.append(data)


class _InitialPropsParser(HTMLParser):
    """Collect only exact Fotocasa initial-props script bodies."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.matches: List[List[str]] = []
        self._active: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        if (
            tag.casefold() == "script"
            and values.get("id") == "__initial_props__"
            and values.get("type", "").casefold() == "application/json"
        ):
            self._active = []
            self.matches.append(self._active)

    def handle_data(self, data):
        if self._active is not None:
            self._active.append(data)

    def handle_endtag(self, tag):
        if tag.casefold() == "script":
            self._active = None


def _node_text(node: _HtmlNode) -> str:
    parts = list(node.text)
    for key in ("data-location", "data-municipality"):
        if node.attrs.get(key):
            parts.append(f"municipio: {node.attrs[key]}")
    if node.attrs.get("itemprop", "").lower() == "addresslocality" and node.attrs.get("content"):
        parts.append(f"municipio: {node.attrs['content']}")
    for child in node.children:
        parts.append(_node_text(child))
    return normalize_space(" ".join(parts))


def _is_card_boundary(node: _HtmlNode) -> bool:
    if node.tag in {"article", "li"}:
        return True
    marker = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}".lower()
    return node.tag in {"div", "section"} and bool(re.search(r"(?:^|[\s_-])(card|listing|property|re-card)(?:$|[\s_-])", marker))


def _nearest_card(node: _HtmlNode) -> _HtmlNode:
    current = node.parent
    while current is not None and current.tag != "document":
        if _is_card_boundary(current):
            return current
        current = current.parent
    return node


def _iter_nodes(roots: Iterable[_HtmlNode]) -> Iterable[_HtmlNode]:
    pending = list(roots)
    while pending:
        node = pending.pop(0)
        pending[0:0] = node.children
        yield node


def _class_tokens(node: _HtmlNode) -> set[str]:
    return {value for value in node.attrs.get("class", "").split() if value}


def _node_payload(node: _HtmlNode) -> str:
    """Scoped markup-like payload used only by the existing plain URL fallback."""
    parts = list(node.attrs.values()) + list(node.text)
    for child in node.children:
        parts.append(_node_payload(child))
    return normalize_space(" ".join(parts))


def _provider_result_roots(parser: _LocalCardParser, portal: str) -> List[_HtmlNode]:
    nodes = list(_iter_nodes(parser.root.children))
    if portal == "fotocasa":
        matches = [
            node for node in nodes
            if node.tag == "section"
            and node.attrs.get("id") == "main-content"
            and node.attrs.get("data-testid") == "re-SearchResult"
        ]
        return matches if len(matches) == 1 else []
    if portal == "habitaclia":
        mains = [
            node for node in nodes
            if node.tag == "main" and node.attrs.get("id") == "js-list"
            and "list-main" in _class_tokens(node)
        ]
        if len(mains) != 1:
            return []
        sections = [
            node for node in _iter_nodes(mains[0].children)
            if node.tag == "section" and "list-items" in _class_tokens(node)
            and "list-ady" not in _class_tokens(node)
        ]
        accepted = []
        for section in sections:
            for node in _iter_nodes(section.children):
                if node.tag != "article" or "js-list-item" not in _class_tokens(node):
                    continue
                ancestor = node.parent
                excluded = False
                while ancestor is not None and ancestor is not mains[0]:
                    if ancestor.tag == "section" and "list-ady" in _class_tokens(ancestor):
                        excluded = True
                        break
                    ancestor = ancestor.parent
                if not excluded:
                    accepted.append(node)
        return accepted
    return []


def strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(c)
    )


def normalize_space(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\\u002F", "/").replace("\\/", "/")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def lower_plain(value: str) -> str:
    return strip_accents(normalize_space(value)).lower()


def canonical_url(value: str) -> str:
    parsed = urlparse(value)
    path = re.sub(r"/+", "/", parsed.path)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def fetch_html(url: str, timeout: int = 25, provider: str = "unknown") -> Dict[str, Any]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.6",
        },
    )
    started = time.time()
    try:
        # Exact network boundary: req.full_url is the request handed to urlopen,
        # not a copy reconstructed from the earlier plan.
        executed_url = req.full_url
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return {
                "ok": True,
                "status": getattr(resp, "status", None),
                "final_url": resp.geturl(),
                "html": text,
                "html_len": len(text),
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": None,
                "query_provenance": query_provenance(
                    provider, url, executed_url, status=getattr(resp, "status", None),
                    final_url=resp.geturl(),
                ),
            }
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.code,
            "final_url": url,
            "html": body,
            "html_len": len(body),
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": f"HTTPError: {exc}",
            "query_provenance": query_provenance(
                provider, url, req.full_url, status=exc.code, final_url=exc.geturl(),
            ),
        }
    except URLError as exc:
        return {
            "ok": False,
            "status": None,
            "final_url": url,
            "html": "",
            "html_len": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": f"URLError: {exc}",
            "query_provenance": query_provenance(provider, url, req.full_url),
        }


def has_withdrawn_signal(text: str) -> bool:
    low = lower_plain(text)
    return any(pattern in low for pattern in [lower_plain(p) for p in WITHDRAWN_PATTERNS])


def extract_price(text: str) -> Optional[int]:
    clean = normalize_space(text)
    patterns = [
        r"(?<![,.\d])(\d{1,7}(?:(?:[.\s]\d{3})+|,\d{1,2})?)\s*€(?!\s*/?\s*m\s*(?:2|²))\s*/?\s*(?:mes|m[eé]s)?",
        r'"rawPrice"\s*:\s*"?(\d{1,7}(?:(?:\.\d{3})+|,\d{1,2}|\.\d{1,2})?)"?',
        r'"price"\s*:\s*"?(\d{1,7}(?:(?:\.\d{3})+|,\d{1,2}|\.\d{1,2})?)"?',
        r'"amount"\s*:\s*"?(\d{1,7}(?:(?:\.\d{3})+|,\d{1,2}|\.\d{1,2})?)"?',
        r"(?:precio|price|amount|rent|monthlyPrice)\s*[:=]\s*\"?(\d{1,7}(?:(?:\.\d{3})+|,\d{1,2}|\.\d{1,2})?)\"?",
    ]
    for pat in patterns:
        for m in re.finditer(pat, clean, flags=re.I):
            value = normalize_euro_price(m.group(1))
            if value is not None and value <= 10000000:
                return int(value)
    return None


def extract_bedrooms(text: str) -> Optional[int]:
    clean = lower_plain(text)
    patterns = [
        r"(\d{1,2})\s*(?:hab\.?|habitaciones|dormitorios|dormitorio)",
        r'"key"\s*:\s*"rooms"\s*,\s*"value"\s*:\s*(\d{1,2})',
        r'"rooms"\s*:\s*(\d{1,2})',
        r"habitaciones\s*:?\s*(\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, clean, flags=re.I)
        if m:
            value = int(m.group(1))
            if 0 <= value <= 20:
                return value
    return None


def extract_area(text: str) -> Optional[int]:
    clean = lower_plain(text)
    patterns = [
        r"(\d{2,5})\s*m\s*(?:2|²)",
        r'"key"\s*:\s*"surface"\s*,\s*"value"\s*:\s*(\d{2,5})',
        r'"surface"\s*:\s*(\d{2,5})',
        r"superficie\s*:?\s*(\d{2,5})",
    ]
    for pat in patterns:
        m = re.search(pat, clean, flags=re.I)
        if m:
            value = int(m.group(1))
            if 10 <= value <= 5000:
                return value
    return None


def compact_evidence(text: str, max_len: int = 280) -> str:
    return normalize_space(text)[:max_len]


def title_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    slug = ""
    for part in reversed(parts):
        if part in {"d", "l", "todas-las-zonas"} or part.isdigit():
            continue
        if len(part) > len(slug):
            slug = part
    slug = slug.replace("-", " ").replace("_", " ")
    slug = re.sub(r"\b\d{6,}\b", "", slug)
    return normalize_space(slug).capitalize()


def property_type_from_listing_evidence(url: str, title: str = "", card_text: str = "") -> Optional[str]:
    """Map only explicit listing URL/title nouns; search intent is excluded."""
    evidence = lower_plain(f"{urlparse(url).path} {title} {card_text}")
    tokens = set(re.findall(r"[a-z]+", evidence))
    if tokens.intersection({"piso", "atico", "duplex", "loft", "estudio", "apartamento"}):
        return "flat"
    if tokens.intersection({"casa", "chalet", "adosado", "pareado"}):
        return "house"
    if tokens.intersection({"terreno", "parcela", "solar"}):
        return "land"
    if tokens.intersection({"local", "oficina", "nave"}):
        return "commercial"
    return None


def extract_same_listing_detail_attributes(
    html_text: str, requested_url: str, final_url: str,
) -> Dict[str, Any]:
    """Extract only JSON-LD attributes owned by the requested listing.

    Identity and listing types deliberately reuse SR0.15C's exact predicates.
    Arbitrary page text, breadcrumbs and recommendation objects are excluded.
    """
    from .search_availability_semantics import (
        _LISTING_TYPES,
        _json_ld_documents,
        _objects,
        _same_listing,
        _structured_urls,
        _types,
    )

    matched = None
    for document in _json_ld_documents(html_text):
        for obj in _objects(document):
            if not (_types(obj.get("@type")) & _LISTING_TYPES):
                continue
            if any(
                _same_listing(requested_url, final_url, value)
                for value in _structured_urls(obj, final_url)
            ):
                matched = obj
                break
        if matched is not None:
            break
    if matched is None:
        return {"identity_matched": False, "attributes": {}}

    offered = matched.get("itemOffered")
    subject = offered if isinstance(offered, dict) else matched
    offers = matched.get("offers") or subject.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers
    offer = offer if isinstance(offer, dict) else {}
    price_spec = offer.get("priceSpecification")
    price_spec = price_spec if isinstance(price_spec, dict) else {}
    price = normalize_euro_price(offer.get("price") or price_spec.get("price"))

    bedrooms = None
    for key in ("numberOfBedrooms", "numberOfRooms", "numberOfBedroomsTotal"):
        raw = subject.get(key)
        try:
            value = int(float(str(raw))) if raw not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        if value is not None and 0 <= value <= 20:
            bedrooms = value
            break

    subject_types = _types(subject.get("@type"))
    if subject_types & {"apartment", "accommodation"}:
        property_type = "flat"
    elif subject_types & {"house", "singlefamilyresidence", "residence"}:
        property_type = "house"
    else:
        property_type = None

    address = subject.get("address") or matched.get("address")
    address = address if isinstance(address, dict) else {}
    municipality = normalize_space(str(address.get("addressLocality") or "")) or None
    province = normalize_space(str(address.get("addressRegion") or "")) or None

    attributes = {
        "price": int(price) if price is not None else None,
        "bedrooms": bedrooms,
        "property_type": property_type,
        "municipality": municipality,
        "province": province,
    }
    return {
        "identity_matched": True,
        "source": "jsonld",
        "attributes": {key: value for key, value in attributes.items() if value not in (None, "")},
    }


def _initial_props_property_type(value: Any) -> Optional[str]:
    normalized = re.sub(r"[^a-z]+", "_", str(value or "").casefold()).strip("_")
    if any(token in normalized.split("_") for token in ("house", "chalet", "casa")):
        return "house"
    if any(token in normalized.split("_") for token in ("flat", "apartment", "piso")):
        return "flat"
    return None


def _explicit_bedroom_counts(texts: Iterable[Any]) -> set[int]:
    counts: set[int] = set()
    for text in texts:
        for match in re.finditer(
            r"(?<!\d)(\d{1,2})\s+(?:habitaci[oó]n|habitaciones|dormitorio|dormitorios)\b",
            str(text or ""), flags=re.I,
        ):
            value = int(match.group(1))
            if 1 <= value <= 20:
                counts.add(value)
    return counts


def _fotocasa_initial_props_same_listing_root(
    html_text: str, requested_url: str, final_url: str,
) -> Optional[Dict[str, Any]]:
    """Return parsed initial props only after the complete SR0.16F identity gate."""
    from .search_availability_semantics import _fotocasa_detail_listing_id

    requested_id = _fotocasa_detail_listing_id(requested_url)
    final_id = _fotocasa_detail_listing_id(final_url)
    if not requested_id or final_id != requested_id:
        return None
    parser = _InitialPropsParser()
    try:
        parser.feed(str(html_text or ""))
    except (TypeError, ValueError):
        return None
    if len(parser.matches) != 1:
        return None
    try:
        root = json.loads("".join(parser.matches[0]).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(root, dict):
        return None
    real_estate = root.get("realEstate")
    detail = root.get("realEstateAdDetailEntityV2")
    if not isinstance(real_estate, dict) or not isinstance(detail, dict):
        return None
    if str(real_estate.get("id") or "") != requested_id \
            or str(detail.get("propertyId") or "") != requested_id:
        return None
    return root


def fotocasa_initial_props_occupancy_is_available(
    html_text: str, requested_url: str, final_url: str,
) -> bool:
    """True only for an unambiguous same-listing IS_AVAILABLE feature set."""
    root = _fotocasa_initial_props_same_listing_root(html_text, requested_url, final_url)
    if root is None:
        return False
    features = root["realEstateAdDetailEntityV2"].get("features")
    if not isinstance(features, list):
        return False
    values = {
        str(feature.get("value") or "").strip().upper()
        for feature in features
        if isinstance(feature, dict)
        and str(feature.get("type") or "").strip().upper() == "OCCUPANCY_STATUS"
    }
    return values == {"IS_AVAILABLE"}


def extract_fotocasa_initial_props_detail_attributes(
    html_text: str, requested_url: str, final_url: str,
) -> Dict[str, Any]:
    """Fail-closed Fotocasa detail attributes from one exact initial-props script."""
    root = _fotocasa_initial_props_same_listing_root(html_text, requested_url, final_url)
    if root is None:
        return {"identity_matched": False, "source": "fotocasa_initial_props", "attributes": {}}
    real_estate = root["realEstate"]
    detail = root["realEstateAdDetailEntityV2"]

    conflicts: Dict[str, bool] = {}
    attributes: Dict[str, Any] = {}

    prices = []
    for raw in (
        real_estate.get("price"),
        detail.get("price", {}).get("amount") if isinstance(detail.get("price"), dict) else None,
    ):
        value = normalize_euro_price(raw)
        if value is not None and 10 <= value <= 10000000:
            prices.append(int(value))
    unique_prices = set(prices)
    if len(unique_prices) == 1:
        attributes["price"] = unique_prices.pop()
    elif len(unique_prices) > 1:
        conflicts["price"] = True

    descriptions = []
    localized = real_estate.get("descriptions")
    if isinstance(localized, dict) and isinstance(localized.get("es-ES"), str):
        descriptions.append(localized["es-ES"])
    if isinstance(detail.get("description"), str):
        descriptions.append(detail["description"])
    bedroom_counts = _explicit_bedroom_counts(descriptions)
    if len(bedroom_counts) == 1:
        attributes["bedrooms"] = bedroom_counts.pop()
    elif len(bedroom_counts) > 1:
        conflicts["bedrooms"] = True

    typologies = []
    if real_estate.get("buildingSubtype"):
        typologies.append(real_estate["buildingSubtype"])
    for feature in real_estate.get("featuresList") or []:
        if isinstance(feature, dict) and str(feature.get("label") or "").casefold() == "typology":
            typologies.append(feature.get("value"))
    for feature in detail.get("features") or []:
        if isinstance(feature, dict) and str(feature.get("type") or "").casefold() == "typology":
            typologies.append(feature.get("value"))
    normalized_types = {_initial_props_property_type(value) for value in typologies}
    normalized_types.discard(None)
    if len(normalized_types) == 1:
        attributes["property_type"] = normalized_types.pop()
    elif len(normalized_types) > 1:
        conflicts["property_type"] = True

    address = real_estate.get("address")
    address = address if isinstance(address, dict) else {}
    municipality = normalize_space(str(address.get("municipality") or "")) or None
    city = normalize_space(str(address.get("city") or "")) or None
    if municipality and city and lower_plain(municipality) != lower_plain(city):
        conflicts["municipality"] = True
    elif municipality or city:
        attributes["municipality"] = municipality or city
    province = normalize_space(str(address.get("province") or "")) or None
    if province:
        attributes["province"] = province

    return {
        "identity_matched": True,
        "source": "fotocasa_initial_props",
        "attributes": attributes,
        "conflicts": conflicts,
    }


def municipality_from_listing_evidence(title: str, card_text: str) -> tuple[Optional[str], str]:
    """Extract explicit local text, then normalize only declared registry identities."""
    patterns = (
        (title, r"\b(?:piso|apartamento|atico|duplex|loft|estudio|casa|chalet|adosado|inmueble)\s+(?:en|de)\s+([A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ .'-]{1,60})"),
        (card_text, r"\b(?:ubicaci[oó]n|localidad|municipio)\s*[:：]\s*([A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ .'-]{1,60})"),
    )
    for source, pattern in patterns:
        match = re.search(pattern, normalize_space(source), flags=re.I)
        if not match:
            continue
        raw = re.split(
            r"\s(?:\||·|—)\s|[,;]|\s+(?=\d|precio\b|habitaciones?\b|dormitorios?\b|superficie\b)",
            match.group(1), maxsplit=1, flags=re.I,
        )[0].strip(" .-")
        if not raw:
            continue
        try:
            from .geography_registry.models import ResolutionStatus
            from .geography_registry.resolver import resolve
            resolved = resolve(raw, expected_type=("municipality", "district"))
            if resolved.status in {ResolutionStatus.EXACT, ResolutionStatus.ALIAS_RESOLVED}:
                return resolved.canonical_name, "title" if source == title else "card"
        except (ImportError, OSError, ValueError):
            pass
        return normalize_space(raw), "title" if source == title else "card"
    return None, "unknown"


def is_weak_title(title: str) -> bool:
    t = normalize_space(title)
    if not t:
        return True
    if re.match(r"^\d+\s*/\s*\d+$", t):
        return True
    if len(t) <= 3:
        return True
    return False


def html_to_text(raw: str) -> str:
    raw = html.unescape(raw or "").replace("\\u002F", "/").replace("\\/", "/")
    raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return normalize_space(raw)


def is_habitaclia_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "habitaclia.com" not in host:
        return False
    if "/alquiler-" not in path or not path.endswith(".htm"):
        return False
    if not re.search(r"-i\d+\.htm$", path):
        return False
    if any(x in path for x in ["/mapa", "/obra-nueva", "/promocion", "/venta-"]):
        return False
    return bool(re.search(r"/alquiler-(piso|casa|atico|ático|duplex|dúplex|chalet|loft|estudio|apartamento|planta_baja|inmueble)-", path))


def is_fotocasa_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "fotocasa.es" not in host:
        return False
    if "/es/alquiler/" not in path:
        return False
    if any(x in path for x in ["/mapa", "/obra-nueva", "/comprar", "/venta", "/promocion"]):
        return False
    if path.endswith("/l") or "/todas-las-zonas/l" in path:
        return False
    return bool(re.search(r"/\d{6,}/d$", path))


def iter_anchor_contexts(html_text: str, base_url: str, portal: str) -> Iterable[Tuple[str, str, str]]:
    expanded = html.unescape(html_text).replace("\\u002F", "/").replace("\\/", "/")
    parser = _LocalCardParser()
    parser.feed(expanded)
    roots = _provider_result_roots(parser, portal)
    for node in _iter_nodes(roots):
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        href = urljoin(base_url, html.unescape(node.attrs["href"]))
        title = _node_text(node)
        yield href, title, _node_text(_nearest_card(node))


def scoped_result_payloads(html_text: str, portal: str) -> List[str]:
    expanded = html.unescape(html_text).replace("\\u002F", "/").replace("\\/", "/")
    parser = _LocalCardParser()
    parser.feed(expanded)
    return [_node_payload(node) for node in _provider_result_roots(parser, portal)]


_PROVIDER_EMPTY_MARKERS = {
    "fotocasa": (
        "no hemos encontrado ningún inmueble",
        "no hemos encontrado ningun inmueble",
    ),
    "habitaclia": (
        "no hemos encontrado anuncios",
        "no hay anuncios para tu búsqueda",
        "no hay anuncios para tu busqueda",
    ),
}


def _provider_no_results_marker(html_text: str, portal: str) -> bool:
    """Recognize only explicit provider empty-result copy, never mere zero anchors."""
    text = lower_plain(html_to_text(html_text))
    return any(marker in text for marker in _PROVIDER_EMPTY_MARKERS.get(portal, ()))


def _listing_anchor_count(html_text: str, base_url: str, portal: str) -> int:
    # Count anchors present in the accredited results scope before URL/parser
    # validation, so "anchors present but none parseable" remains observable.
    return sum(1 for _href, _title, _context in iter_anchor_contexts(
        html_text, base_url, portal,
    ))


def iter_plain_url_contexts(html_text: str, base_url: str, portal: str) -> Iterable[Tuple[str, str, str]]:
    expanded = html.unescape(html_text).replace("\\u002F", "/").replace("\\/", "/")

    if portal == "habitaclia":
        pattern = r"(?:https?:)?//www\.habitaclia\.com/alquiler-[^\"'\s<>]+?-i\d+\.htm|/alquiler-[^\"'\s<>]+?-i\d+\.htm"
    elif portal == "fotocasa":
        pattern = r"(?:https?:)?//www\.fotocasa\.es/es/alquiler/[^\"'\s<>]+?/\d+/d|/es/alquiler/[^\"'\s<>]+?/\d+/d"
    else:
        return

    for m in re.finditer(pattern, expanded, flags=re.I):
        raw_url = m.group(0)
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        href = urljoin(base_url, raw_url)
        # A plain URL has no proven structural owner. Keep URL evidence only;
        # a global character window could mix adjacent listings.
        yield href, "", ""


def candidate_from_context(
    portal: str,
    source_provider: str,
    url: str,
    title: str,
    context: str,
    municipality: Optional[str],
) -> PortalCandidate:
    url = canonical_url(url)
    safe_title = title_from_url(url) if is_weak_title(title) else title
    price = extract_price(normalize_space(" ".join([title, context])))
    bedrooms = extract_bedrooms(normalize_space(" ".join([title, context])))
    area_m2 = extract_area(normalize_space(" ".join([title, context])))
    type_from_url_title = property_type_from_listing_evidence(url, safe_title)
    property_type = type_from_url_title or property_type_from_listing_evidence("", "", context)
    candidate_municipality, location_source = municipality_from_listing_evidence(safe_title, context)

    return PortalCandidate(
        portal=portal,
        source_provider=source_provider,
        title=safe_title or compact_evidence(context, 120) or url,
        source_url=url,
        price=price,
        bedrooms=bedrooms,
        area_m2=area_m2,
        property_type=property_type,
        # Requested geography and URL structure are discovery provenance, not
        # candidate locality evidence. No structured locality is available here.
        municipality=candidate_municipality,
        municipality_match=False,
        evidence=compact_evidence(context),
        candidate_evidence={
            "price": "card" if price is not None else "unknown",
            "bedrooms": "card" if bedrooms is not None else "unknown",
            "area_m2": "card" if area_m2 is not None else "unknown",
            "property_type": (
                "url_or_title" if type_from_url_title else
                "card" if property_type else "unknown"
            ),
            "location": location_source,
        },
    )


def candidate_score(c: PortalCandidate) -> int:
    score = 0
    score += 5 if c.price else 0
    score += 3 if c.bedrooms is not None else 0
    score += 3 if c.area_m2 else 0
    score += 1 if c.title and not is_weak_title(c.title) else 0
    return score


def merge_candidate(base: PortalCandidate, other: PortalCandidate) -> PortalCandidate:
    if not base.price and other.price:
        base.price = other.price
    if base.bedrooms is None and other.bedrooms is not None:
        base.bedrooms = other.bedrooms
    if not base.area_m2 and other.area_m2:
        base.area_m2 = other.area_m2
    if not base.municipality and other.municipality:
        base.municipality = other.municipality
    base.municipality_match = base.municipality_match or other.municipality_match

    if is_weak_title(base.title) and not is_weak_title(other.title):
        base.title = other.title
    elif candidate_score(other) > candidate_score(base) and not is_weak_title(other.title):
        base.title = other.title

    if other.evidence and len(other.evidence) > len(base.evidence or ""):
        base.evidence = other.evidence
    return base


def dedupe_candidates(candidates: Iterable[PortalCandidate]) -> List[PortalCandidate]:
    by_url: Dict[str, PortalCandidate] = {}
    for c in candidates:
        key = canonical_url(c.source_url)
        if key not in by_url:
            by_url[key] = c
        else:
            by_url[key] = merge_candidate(by_url[key], c)
    return sorted(by_url.values(), key=candidate_score, reverse=True)


def extract_habitaclia_candidates(html_text: str, base_url: str, municipality: Optional[str] = None) -> Dict[str, Any]:
    candidates: List[PortalCandidate] = []
    discarded: List[Dict[str, str]] = []

    result_payloads = scoped_result_payloads(html_text, "habitaclia")
    streams = list(iter_anchor_contexts(html_text, base_url, "habitaclia"))
    for payload in result_payloads:
        streams.extend(iter_plain_url_contexts(payload, base_url, "habitaclia"))

    for href, title, ctx in streams:
        if not is_habitaclia_listing_url(href):
            continue

        candidates.append(candidate_from_context("habitaclia", "deterministic_habitaclia", href, title, ctx, municipality))

    final_candidates = dedupe_candidates(candidates)

    return {
        "portal": "habitaclia",
        "source_provider": "deterministic_habitaclia",
        "withdrawn_signal": has_withdrawn_signal(html_text),
        "result_scope_found": bool(result_payloads),
        "listing_anchor_count": _listing_anchor_count(html_text, base_url, "habitaclia"),
        "no_results_marker_found": _provider_no_results_marker(html_text, "habitaclia"),
        "candidates": [asdict(c) for c in final_candidates],
        "discarded": discarded[:80],
    }


def extract_fotocasa_candidates(html_text: str, base_url: str, municipality: Optional[str] = None) -> Dict[str, Any]:
    candidates: List[PortalCandidate] = []
    discarded: List[Dict[str, str]] = []

    parsed_base = urlparse(base_url)
    is_listing_page = parsed_base.path.endswith("/l") or "/todas-las-zonas/l" in parsed_base.path

    result_payloads = scoped_result_payloads(html_text, "fotocasa")
    streams = list(iter_anchor_contexts(html_text, base_url, "fotocasa"))
    for payload in result_payloads:
        streams.extend(iter_plain_url_contexts(payload, base_url, "fotocasa"))

    for href, title, ctx in streams:
        if not is_fotocasa_listing_url(href):
            continue

        candidates.append(candidate_from_context("fotocasa", "deterministic_fotocasa", href, title, ctx, municipality))

    final_candidates = dedupe_candidates(candidates)

    return {
        "portal": "fotocasa",
        "source_provider": "deterministic_fotocasa",
        "withdrawn_signal": False if is_listing_page else has_withdrawn_signal(html_text),
        "result_scope_found": bool(result_payloads),
        "listing_anchor_count": _listing_anchor_count(html_text, base_url, "fotocasa"),
        "no_results_marker_found": _provider_no_results_marker(html_text, "fotocasa"),
        "candidates": [asdict(c) for c in final_candidates],
        "discarded": discarded[:80],
    }


def probe_portal_url(portal: str, url: str, municipality: Optional[str] = None, timeout: int = 25) -> Dict[str, Any]:
    fetched = fetch_html(url, timeout=timeout, provider=portal)
    html_text = fetched.get("html") or ""

    if portal == "habitaclia":
        extracted = extract_habitaclia_candidates(html_text, fetched.get("final_url") or url, municipality)
    elif portal == "fotocasa":
        extracted = extract_fotocasa_candidates(html_text, fetched.get("final_url") or url, municipality)
    else:
        raise ValueError(f"Portal no soportado: {portal}")

    candidates = extracted.get("candidates", [])
    status = fetched.get("status")
    final_url = fetched.get("final_url") or url
    requested_location = urlparse(url)
    final_location = urlparse(final_url)
    unexpected_redirect = bool(
        final_url
        and (requested_location.hostname, requested_location.path)
        != (final_location.hostname, final_location.path)
    )
    scope_found = bool(extracted.get("result_scope_found"))
    anchor_count = int(extracted.get("listing_anchor_count") or 0)
    no_results_marker = bool(extracted.get("no_results_marker_found"))
    if not isinstance(status, int) or not 200 <= status < 400:
        zero_reason = "HTTP_NON_SUCCESS"
    elif unexpected_redirect:
        zero_reason = "UNEXPECTED_REDIRECT"
    elif not scope_found:
        zero_reason = "RESULT_SCOPE_NOT_FOUND"
    elif candidates:
        zero_reason = "NONZERO"
    elif no_results_marker:
        zero_reason = "PROVIDER_EMPTY_CONFIRMED"
    elif anchor_count == 0:
        zero_reason = "SCOPE_FOUND_NO_ANCHORS"
    else:
        zero_reason = "ANCHORS_FOUND_PARSE_ZERO"
    result_telemetry = {
        "result_scope_expected": True,
        "result_scope_found": scope_found,
        "listing_anchor_count": anchor_count,
        "parsed_candidate_count": len(candidates),
        "no_results_marker_found": no_results_marker,
        "zero_reason": zero_reason,
    }
    provenance = fetched.get("query_provenance")
    if isinstance(provenance, dict):
        response = provenance.setdefault("response", {})
        response.update(result_telemetry)
        response["result_telemetry"] = dict(result_telemetry)

    return {
        "write_db": False,
        "portal": portal,
        "requested_url": url,
        "final_url": fetched.get("final_url"),
        "http_ok": fetched.get("ok"),
        "http_status": fetched.get("status"),
        "html_len": fetched.get("html_len"),
        "elapsed_ms": fetched.get("elapsed_ms"),
        "fetch_error": fetched.get("error"),
        "withdrawn_signal": extracted.get("withdrawn_signal"),
        "result_scope_found": bool(extracted.get("result_scope_found")),
        "result_telemetry": result_telemetry,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "discarded_preview": extracted.get("discarded", [])[:30],
        "query_provenance": provenance,
    }
