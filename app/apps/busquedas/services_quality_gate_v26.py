from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from apps.busquedas.services_portal_extractors import (
    has_withdrawn_signal,
    is_fotocasa_listing_url,
    is_habitaclia_listing_url,
    lower_plain,
)


@dataclass
class QualityGateV26Result:
    decision: str
    status_target: str
    reason: str
    warnings: list[str]
    candidate: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def to_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def is_individual_portal_url(candidate: Dict[str, Any]) -> bool:
    portal = (candidate.get("portal") or "").lower()
    url = candidate.get("source_url") or ""

    if portal == "habitaclia":
        return is_habitaclia_listing_url(url)
    if portal == "fotocasa":
        return is_fotocasa_listing_url(url)

    path = urlparse(url).path.lower()
    return bool(path and not path.endswith("/l"))


def evaluate_candidate_v26(
    candidate: Dict[str, Any],
    *,
    municipality: Optional[str],
    min_price: Any = None,
    max_price: Any = None,
    min_bedrooms: Any = None,
    min_area_m2: Any = None,
    page_withdrawn_signal: bool = False,
) -> QualityGateV26Result:
    warnings: list[str] = []

    url = candidate.get("source_url") or ""
    price = to_decimal(candidate.get("price"))
    bedrooms = candidate.get("bedrooms")
    area_m2 = to_decimal(candidate.get("area_m2"))

    min_price_d = to_decimal(min_price)
    max_price_d = to_decimal(max_price)
    min_area_d = to_decimal(min_area_m2)

    if not url:
        return QualityGateV26Result("discarded", "discarded", "missing_url", warnings, candidate)

    if not is_individual_portal_url(candidate):
        return QualityGateV26Result("discarded", "discarded", "not_individual_listing_url", warnings, candidate)

    if municipality:
        candidate_municipality = lower_plain(str(candidate.get("municipality") or ""))
        if candidate_municipality and candidate_municipality != lower_plain(municipality):
            return QualityGateV26Result("discarded", "discarded", "location_outside_search_scope", warnings, candidate)
        if not candidate_municipality:
            warnings.append("unknown_required_attribute:location")

    if page_withdrawn_signal or has_withdrawn_signal(candidate.get("evidence") or ""):
        return QualityGateV26Result("discarded", "discarded", "withdrawn_signal", warnings, candidate)

    if price is None and (min_price_d is not None or max_price_d is not None):
        warnings.append("unknown_required_attribute:price")

    if price is not None and min_price_d is not None and price < min_price_d:
        return QualityGateV26Result("discarded", "discarded", "price_below_min", warnings, candidate)

    if price is not None and max_price_d is not None and price > max_price_d:
        return QualityGateV26Result("discarded", "discarded", "price_above_max", warnings, candidate)

    complete = True

    if min_bedrooms not in (None, ""):
        try:
            min_bedrooms_i = int(min_bedrooms)
        except Exception:
            min_bedrooms_i = None

        if min_bedrooms_i is not None:
            if bedrooms is None:
                warnings.append("unknown_required_attribute:bedrooms")
                complete = False
            else:
                try:
                    bedrooms_i = int(bedrooms)
                except (TypeError, ValueError):
                    warnings.append("unknown_required_attribute:bedrooms")
                    complete = False
                else:
                    if bedrooms_i < min_bedrooms_i:
                        return QualityGateV26Result("discarded", "discarded", "bedrooms_below_min", warnings, candidate)

    if min_area_d is not None:
        if area_m2 is None:
            warnings.append("unknown_required_attribute:area_m2")
            complete = False
        elif area_m2 < min_area_d:
            return QualityGateV26Result("discarded", "discarded", "area_below_min", warnings, candidate)

    if warnings:
        complete = False

    if complete:
        return QualityGateV26Result("verified", "captured", "deterministic_portal_confirmed", warnings, candidate)

    return QualityGateV26Result("reviewable", "in_review", ";".join(warnings) or "partial_deterministic_data", warnings, candidate)


def summarize_quality_gate(results: list[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "version": "sooi_v2_6",
        "total_candidates": len(results),
        "verified": 0,
        "reviewable": 0,
        "discarded": 0,
    }
    for item in results:
        decision = item.get("decision")
        if decision in summary:
            summary[decision] += 1
    summary["total_valid_candidates"] = summary["verified"] + summary["reviewable"]
    summary["total_found"] = summary["total_valid_candidates"]
    summary["total_errors"] = summary["discarded"]
    return summary
