import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse, urlunparse

from openai import OpenAI

from apps.ia.models import AIProviderConfig


@dataclass
class AIDiscoveryItem:
    source_name: str
    source_url: str
    title: str
    property_type: str
    province: str
    municipality: str = ""
    price: Decimal | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_m2: Decimal | None = None
    summary: str = ""
    raw_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIDiscoveryResult:
    provider: str
    model_name: str
    query_text: str
    filters_snapshot: dict[str, Any]
    raw_response: dict[str, Any]
    warnings: list[str]
    items: list[AIDiscoveryItem]


def _clean_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _normalize_property_type(value: str) -> str:
    val = (value or "").strip().lower()
    if val in {"house", "casa", "chalet", "adosado", "vivienda"}:
        return "house"
    if val in {"flat", "piso", "apartamento", "apartament"}:
        return "flat"
    if val in {"land", "terreno", "solar", "parcela"}:
        return "land"
    if val in {"commercial", "local", "local comercial", "negocio"}:
        return "commercial"
    return "house"


def _operation_label(value: str) -> str:
    return {
        "sale": "venta",
        "rent": "alquiler",
    }.get(value, value or "sin definir")


def _property_type_labels(values: list[str]) -> str:
    labels = {
        "house": "casa",
        "flat": "piso",
        "land": "terreno",
        "commercial": "local",
    }
    if not values:
        return "sin definir"
    return ", ".join(labels.get(v, v) for v in values)


def _canonical_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlparse(value.strip())
        clean = parsed._replace(query="", fragment="")
        url = urlunparse(clean)
        if url.endswith("/"):
            url = url[:-1]
        return url
    except Exception:
        return value.strip()


def _stringify_error(exc: Exception) -> str:
    try:
        return str(exc)
    except Exception:
        return repr(exc)


def _classify_provider_error(exc: Exception) -> str:
    text = _stringify_error(exc).lower()

    if (
        "insufficient_quota" in text
        or "current quota" in text
        or "billing" in text
        or "credit balance" in text
        or ("429" in text and "quota" in text)
    ):
        return "quota"

    if "rate limit" in text or "too many requests" in text:
        return "rate_limit"

    return "provider_error"


def _unique_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class AIDiscoveryClient:
    provider_name = "ai"

    def __init__(self) -> None:
        pass

    def _get_provider_specs(self) -> list[dict[str, Any]]:
        configs = list(
            AIProviderConfig.objects.filter(is_active=True)
            .order_by("-is_default", "priority_order", "id")
        )

        if configs:
            return [
                {
                    "name": cfg.name,
                    "provider_code": cfg.provider_code,
                    "model_name": cfg.model_name,
                    "base_url": (cfg.base_url or "").strip(),
                    "api_key": (cfg.api_key or "").strip(),
                    "supports_web_search": cfg.supports_web_search,
                    "supports_reasoning": cfg.supports_reasoning,
                    "priority_order": cfg.priority_order,
                }
                for cfg in configs
            ]

        return []

    def _build_client(self, provider_spec: dict[str, Any]) -> OpenAI:
        kwargs: dict[str, Any] = {
            "api_key": provider_spec["api_key"],
        }
        if provider_spec["base_url"]:
            kwargs["base_url"] = provider_spec["base_url"]
        return OpenAI(**kwargs)

    def _build_search_variants(
        self,
        *,
        operation_type: str,
        province: str,
        zone: str,
        property_types: list[str],
        max_price: Decimal | None,
        min_bedrooms: int | None,
        ai_prompt: str,
    ) -> list[dict[str, str]]:
        operation_text = _operation_label(operation_type)
        property_types_text = _property_type_labels(property_types)
        zone_text = zone.strip()

        variants: list[dict[str, str]] = [
            {
                "label": "general-provincia",
                "focus": (
                    f"Búsqueda general en {province} para {operation_text}, "
                    f"tipologías {property_types_text}, precio máximo "
                    f"{max_price if max_price is not None else 'sin definir'}, "
                    f"dormitorios mínimos {min_bedrooms if min_bedrooms is not None else 'sin definir'}."
                ),
            }
        ]

        if zone_text:
            variants.append(
                {
                    "label": "zona-especifica",
                    "focus": (
                        f"Búsqueda específica en la zona o municipio '{zone_text}' "
                        f"dentro de {province} para {operation_text}. "
                        f"Evita resultados genéricos fuera de esa zona salvo evidencia clara."
                    ),
                }
            )

        variants.append(
            {
                "label": "servicers-banca",
                "focus": (
                    f"Prioriza fuentes de servicers, banca o activos financieros "
                    f"en {province}"
                    f"{f' y zona {zone_text}' if zone_text else ''}. "
                    f"Ejemplos: Solvia, Servihabitat, Haya, Altamira, Sareb y equivalentes."
                ),
            }
        )

        if property_types:
            variants.append(
                {
                    "label": "tipologia-prioritaria",
                    "focus": (
                        f"Búsqueda priorizando especialmente estas tipologías: "
                        f"{property_types_text} en {province}"
                        f"{f' y zona {zone_text}' if zone_text else ''}."
                    ),
                }
            )

        if max_price is not None:
            variants.append(
                {
                    "label": "precio-ajustado",
                    "focus": (
                        f"Búsqueda muy estricta de oportunidades por debajo de "
                        f"{max_price} euros en {province}"
                        f"{f' y zona {zone_text}' if zone_text else ''}."
                    ),
                }
            )

        if ai_prompt.strip():
            variants.append(
                {
                    "label": "contexto-usuario",
                    "focus": (
                        f"Búsqueda orientada por este contexto adicional del usuario: "
                        f"{ai_prompt.strip()}"
                    ),
                }
            )

        return variants[:5]

    def _build_prompt(
        self,
        *,
        operation_type: str,
        province: str,
        zone: str,
        property_types: list[str],
        max_price: Decimal | None,
        min_bedrooms: int | None,
        ai_prompt: str,
        variant_label: str,
        variant_focus: str,
    ) -> str:
        operation_text = _operation_label(operation_type)
        property_types_text = _property_type_labels(property_types)
        zone_text = zone.strip() if zone else "sin definir"

        return f"""
Actúa como motor de descubrimiento inmobiliario para SOOI.

Busca oportunidades inmobiliarias REALES en portales públicos de España.

Parámetros base:
- Operación: {operation_text}
- Provincia: {province}
- Zona / municipio objetivo: {zone_text}
- Tipologías objetivo: {property_types_text}
- Precio máximo: {max_price if max_price is not None else "sin definir"}
- Dormitorios mínimos: {min_bedrooms if min_bedrooms is not None else "sin definir"}

Contexto adicional del usuario:
{ai_prompt if ai_prompt else "Sin texto adicional."}

Subbúsqueda actual:
- Variante: {variant_label}
- Objetivo táctico: {variant_focus}

Reglas obligatorias:
- Devuelve SOLO anuncios reales con URL real y accesible de la ficha del inmueble.
- No inventes URLs, portales, precios, ubicaciones ni características.
- No devuelvas páginas genéricas de búsqueda, listados, home del portal o enlaces ambiguos.
- Si no estás seguro, excluye el resultado.
- Si se ha indicado zona o municipio, prioriza esa zona.
- Si la operación es alquiler, no mezcles venta.
- Si la operación es venta, no mezcles alquiler.
- Máximo 5 resultados para esta subbúsqueda.
- Prioriza calidad y trazabilidad por encima de cantidad.

Devuelve SOLO JSON válido con esta estructura exacta:
{{
  "warnings": ["..."],
  "items": [
    {{
      "source_name": "idealista",
      "source_url": "https://....",
      "title": "....",
      "property_type": "house|flat|land|commercial",
      "province": "{province}",
      "municipality": "....",
      "price": 12345,
      "bedrooms": 2,
      "bathrooms": 1,
      "area_m2": 80,
      "summary": "....",
      "raw_evidence": {{
        "portal": "....",
        "operation_type": "{operation_type}",
        "variant": "{variant_label}",
        "notes": "...."
      }}
    }}
  ]
}}
""".strip()

    def _discover_once(
        self,
        *,
        provider_spec: dict[str, Any],
        operation_type: str,
        province: str,
        zone: str,
        property_types: list[str],
        max_price: Decimal | None,
        min_bedrooms: int | None,
        ai_prompt: str,
        variant_label: str,
        variant_focus: str,
    ) -> tuple[str, dict[str, Any], list[str], list[AIDiscoveryItem], str]:
        provider_name = provider_spec["name"]
        provider_code = provider_spec["provider_code"]

        prompt = self._build_prompt(
            operation_type=operation_type,
            province=province,
            zone=zone,
            property_types=property_types,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            ai_prompt=ai_prompt,
            variant_label=variant_label,
            variant_focus=variant_focus,
        )

        if not provider_spec["api_key"]:
            return (
                prompt,
                {"provider": provider_name, "error": "missing_api_key"},
                [f"[{provider_name}] IA no disponible: falta API key configurada."],
                [],
                "config",
            )

        if not provider_spec["supports_web_search"]:
            return (
                prompt,
                {"provider": provider_name, "error": "web_search_disabled"},
                [f"[{provider_name}] IA no disponible para captación: búsqueda web desactivada."],
                [],
                "unsupported",
            )

        if provider_code != "openai":
            return (
                prompt,
                {"provider": provider_name, "error": "web_search_not_implemented"},
                [f"[{provider_name}] Proveedor configurado, pero la integración de búsqueda web aún no está implementada en SOOI."],
                [],
                "unsupported",
            )

        try:
            client = self._build_client(provider_spec)
            response = client.responses.create(
                model=provider_spec["model_name"],
                tools=[{"type": "web_search"}],
                input=prompt,
            )
            output_text = _clean_json_block(response.output_text)
        except Exception as e:
            error_kind = _classify_provider_error(e)
            raw_error = _stringify_error(e)

            if error_kind == "quota":
                warning = f"[{provider_name}] IA no disponible temporalmente: el proveedor no tiene saldo o ha alcanzado su cuota."
            elif error_kind == "rate_limit":
                warning = f"[{provider_name}] IA temporalmente saturada: el proveedor ha alcanzado su límite de peticiones."
            else:
                warning = f"[{provider_name}] Error del proveedor IA."

            return (
                prompt,
                {
                    "provider": provider_name,
                    "error_kind": error_kind,
                    "error": raw_error,
                },
                [warning],
                [],
                error_kind,
            )

        try:
            payload = json.loads(output_text)
        except Exception:
            return (
                prompt,
                {
                    "provider": provider_name,
                    "error_kind": "invalid_json",
                    "output_text": output_text,
                },
                [f"[{provider_name} | {variant_label}] La respuesta no devolvió JSON válido."],
                [],
                "invalid_json",
            )

        warnings = [
            f"[{provider_name} | {variant_label}] {w}"
            for w in (payload.get("warnings", []) or [])
        ]
        raw_items = payload.get("items", []) or []

        items: list[AIDiscoveryItem] = []

        for raw in raw_items:
            source_url = (raw.get("source_url") or "").strip()
            if not source_url.startswith("http"):
                continue

            items.append(
                AIDiscoveryItem(
                    source_name=(raw.get("source_name") or "desconocida").strip(),
                    source_url=source_url,
                    title=(raw.get("title") or "Sin título").strip(),
                    property_type=_normalize_property_type(raw.get("property_type", "")),
                    province=(raw.get("province") or province).strip(),
                    municipality=(raw.get("municipality") or "").strip(),
                    price=_to_decimal(raw.get("price")),
                    bedrooms=_to_int(raw.get("bedrooms")),
                    bathrooms=_to_int(raw.get("bathrooms")),
                    area_m2=_to_decimal(raw.get("area_m2")),
                    summary=(raw.get("summary") or "").strip(),
                    raw_evidence=raw.get("raw_evidence", {}) or {},
                )
            )

        return (
            prompt,
            {
                "provider": provider_name,
                "output_text": output_text,
            },
            warnings,
            items,
            "success",
        )

    def discover(
        self,
        *,
        operation_type: str,
        province: str,
        zone: str = "",
        property_types: list[str],
        max_price: Decimal | None,
        min_bedrooms: int | None,
        ai_prompt: str = "",
    ) -> AIDiscoveryResult:
        provider_specs = self._get_provider_specs()

        search_variants = self._build_search_variants(
            operation_type=operation_type,
            province=province,
            zone=zone,
            property_types=property_types,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            ai_prompt=ai_prompt,
        )

        filters_snapshot = {
            "operation_type": operation_type,
            "province": province,
            "zone": zone,
            "property_types": property_types,
            "max_price": str(max_price) if max_price is not None else None,
            "min_bedrooms": min_bedrooms,
            "ai_prompt": ai_prompt,
            "search_variants": search_variants,
        }

        if not provider_specs:
            return AIDiscoveryResult(
                provider="unconfigured",
                model_name="",
                query_text="",
                filters_snapshot=filters_snapshot,
                raw_response={"searches": []},
                warnings=["IA no disponible: no hay proveedor IA configurado."],
                items=[],
            )

        all_prompts: list[str] = []
        all_warnings: list[str] = []
        raw_searches: list[dict[str, Any]] = []
        deduped_items: list[AIDiscoveryItem] = []
        seen_urls: set[str] = set()

        successful_provider_codes: list[str] = []
        successful_model_names: list[str] = []

        for variant in search_variants:
            variant_completed = False

            for idx, provider_spec in enumerate(provider_specs):
                prompt, raw_response, warnings, items, status = self._discover_once(
                    provider_spec=provider_spec,
                    operation_type=operation_type,
                    province=province,
                    zone=zone,
                    property_types=property_types,
                    max_price=max_price,
                    min_bedrooms=min_bedrooms,
                    ai_prompt=ai_prompt,
                    variant_label=variant["label"],
                    variant_focus=variant["focus"],
                )

                all_prompts.append(prompt)
                all_warnings.extend(warnings)

                raw_searches.append(
                    {
                        "provider_name": provider_spec["name"],
                        "provider_code": provider_spec["provider_code"],
                        "model_name": provider_spec["model_name"],
                        "variant": variant,
                        "status": status,
                        "items_count": len(items),
                        "response": raw_response,
                    }
                )

                if status == "success":
                    successful_provider_codes.append(provider_spec["provider_code"])
                    successful_model_names.append(provider_spec["model_name"])

                    for item in items:
                        key = _canonical_url(item.source_url)
                        if not key or key in seen_urls:
                            continue
                        seen_urls.add(key)
                        deduped_items.append(item)

                    variant_completed = True
                    break

                is_last_provider = idx == len(provider_specs) - 1

                if len(provider_specs) == 1 and status in {"quota", "config", "unsupported"}:
                    return AIDiscoveryResult(
                        provider=provider_spec["provider_code"],
                        model_name=provider_spec["model_name"],
                        query_text="\n\n---\n\n".join(all_prompts),
                        filters_snapshot=filters_snapshot,
                        raw_response={"searches": raw_searches},
                        warnings=_unique_list(all_warnings),
                        items=[],
                    )

                if is_last_provider and status in {"quota", "config", "unsupported"}:
                    variant_completed = True
                    break

            if not variant_completed:
                continue

        provider_result = "multiple" if len(set(successful_provider_codes)) > 1 else (
            successful_provider_codes[0] if successful_provider_codes else provider_specs[0]["provider_code"]
        )
        model_result = "multiple" if len(set(successful_model_names)) > 1 else (
            successful_model_names[0] if successful_model_names else provider_specs[0]["model_name"]
        )

        return AIDiscoveryResult(
            provider=provider_result,
            model_name=model_result,
            query_text="\n\n---\n\n".join(all_prompts),
            filters_snapshot=filters_snapshot,
            raw_response={"searches": raw_searches},
            warnings=_unique_list(all_warnings),
            items=deduped_items[:12],
        )