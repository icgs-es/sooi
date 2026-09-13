from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.inmuebles.models import CapturedProperty
from apps.busquedas.services_portal_extractors import probe_portal_url
from apps.busquedas.services_quality_gate_v26 import (
    evaluate_candidate_v26,
    summarize_quality_gate,
)


def slug_hyphen(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower().strip()).strip("-")


def slug_underscore(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower().strip()).strip("_")


def to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)))
    except Exception:
        return None


def get_profile_municipality(profile: SearchProfile, explicit: Optional[str]) -> str:
    if explicit:
        return explicit.strip()

    zone = (getattr(profile, "zone", "") or "").strip()
    if zone:
        return zone

    province = (getattr(profile, "province", "") or "").strip()
    if province:
        return province

    raise CommandError("No se pudo resolver municipio/zona desde SearchProfile. Usa --municipality.")


def build_urls(profile: SearchProfile, municipality: str) -> Dict[str, str]:
    operation = getattr(profile, "operation_type", "")
    if operation != SearchProfile.OperationType.RENT:
        raise CommandError("V2.6 inicial solo soporta alquiler/rent para extractores deterministas.")

    params_h = {}
    min_bedrooms = to_int(getattr(profile, "min_bedrooms", None))
    min_area = to_int(getattr(profile, "min_area_m2", None))
    max_price = to_int(getattr(profile, "max_price", None))

    if min_bedrooms:
        params_h["hab"] = min_bedrooms
    if min_area:
        params_h["m2"] = min_area
    if max_price:
        params_h["pmax"] = max_price

    habitaclia = f"https://www.habitaclia.com/alquiler-{slug_underscore(municipality)}.htm"
    if params_h:
        habitaclia += "?" + urlencode(params_h)

    params_f = {"sortType": "publicationDate"}
    if min_bedrooms:
        params_f["minRooms"] = min_bedrooms

    fotocasa = (
        f"https://www.fotocasa.es/es/alquiler/viviendas/"
        f"{slug_hyphen(municipality)}/todas-las-zonas/l"
        f"?{urlencode(params_f)}"
    )

    return {
        "habitaclia": habitaclia,
        "fotocasa": fotocasa,
    }


def source_model():
    return CapturedProperty._meta.get_field("source").remote_field.model


def find_source_for_portal(portal: str):
    Source = source_model()
    fields = {f.name for f in Source._meta.fields}

    q = Q()
    for field in ["name", "code", "slug", "domain", "base_url", "url", "website"]:
        if field in fields:
            q |= Q(**{f"{field}__icontains": portal})

    if not q:
        return None

    return Source.objects.filter(q).order_by("id").first()


def external_id_from_url(portal: str, url: str) -> str:
    if portal == "habitaclia":
        m = re.search(r"-i(\d+)\.htm", url)
        return m.group(1) if m else ""
    if portal == "fotocasa":
        m = re.search(r"/(\d+)/d$", url)
        return m.group(1) if m else ""
    return ""


def captured_status_for_gate(decision: str) -> str:
    if decision == "verified":
        return CapturedProperty.Status.CAPTURED
    if decision == "reviewable":
        return CapturedProperty.Status.IN_REVIEW
    return CapturedProperty.Status.DISCARDED


def property_type_from_profile(profile: SearchProfile) -> str:
    values = getattr(profile, "property_types", None) or []
    if isinstance(values, list) and values:
        return values[0]
    return CapturedProperty.PropertyType.FLAT


def decimal_or_none(value: Any):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


class Command(BaseCommand):
    help = "SOOI V2.6: descubrimiento determinista Habitaclia/Fotocasa con Quality Gate y commit opcional."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, default=None)
        parser.add_argument("--municipality", default=None)
        parser.add_argument("--habitaclia-url", default=None)
        parser.add_argument("--fotocasa-url", default=None)
        parser.add_argument("--timeout", type=int, default=25)
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--list-active", action="store_true")
        parser.add_argument("--inspect-sources", action="store_true")

    def handle(self, *args, **options):
        if options["inspect_sources"]:
            Source = source_model()
            rows = []
            for obj in Source.objects.all().order_by("id")[:80]:
                data = {"id": obj.id}
                for field in ["name", "code", "slug", "domain", "base_url", "url", "website", "is_verified", "is_active"]:
                    if hasattr(obj, field):
                        data[field] = getattr(obj, field)
                rows.append(data)
            self.stdout.write(json.dumps({"sources": rows}, ensure_ascii=False, indent=2))
            return

        if options["list_active"]:
            rows = []
            qs = SearchProfile.objects.select_related("owner").order_by("-id")[:80]
            for p in qs:
                rows.append({
                    "id": p.id,
                    "name": p.name,
                    "owner": getattr(p.owner, "username", str(p.owner)),
                    "operation_type": p.operation_type,
                    "province": p.province,
                    "zone": p.zone,
                    "min_price": str(p.min_price) if p.min_price is not None else None,
                    "max_price": str(p.max_price) if p.max_price is not None else None,
                    "min_bedrooms": p.min_bedrooms,
                    "min_area_m2": str(p.min_area_m2) if p.min_area_m2 is not None else None,
                    "status": p.status,
                })
            self.stdout.write(json.dumps({"search_profiles": rows}, ensure_ascii=False, indent=2))
            return

        if not options["profile_id"]:
            raise CommandError("Usa --profile-id o primero --list-active.")

        profile = SearchProfile.objects.select_related("owner").get(pk=options["profile_id"])
        municipality = get_profile_municipality(profile, options.get("municipality"))

        built_urls = build_urls(profile, municipality)
        urls = {
            "habitaclia": options.get("habitaclia_url") or built_urls["habitaclia"],
            "fotocasa": options.get("fotocasa_url") or built_urls["fotocasa"],
        }

        source_by_portal = {
            "habitaclia": find_source_for_portal("habitaclia"),
            "fotocasa": find_source_for_portal("fotocasa"),
        }

        missing_sources = [k for k, v in source_by_portal.items() if v is None]
        if options["commit"] and missing_sources:
            raise CommandError(f"No se puede hacer commit. Faltan Source para: {', '.join(missing_sources)}")

        portal_results = [
            probe_portal_url("habitaclia", urls["habitaclia"], municipality=municipality, timeout=options["timeout"]),
            probe_portal_url("fotocasa", urls["fotocasa"], municipality=municipality, timeout=options["timeout"]),
        ]

        evaluated = []
        sources = []
        warnings = []

        for portal_result in portal_results:
            portal = portal_result.get("portal")
            sources.append({
                "portal": portal,
                "source_provider": f"deterministic_{portal}",
                "url": urls.get(portal),
                "http_status": portal_result.get("http_status"),
                "candidate_count": portal_result.get("candidate_count"),
                "withdrawn_signal": portal_result.get("withdrawn_signal"),
                "source_id": source_by_portal.get(portal).id if source_by_portal.get(portal) else None,
            })

            for candidate in portal_result.get("candidates", []):
                gate = evaluate_candidate_v26(
                    candidate,
                    municipality=municipality,
                    min_price=getattr(profile, "min_price", None),
                    max_price=getattr(profile, "max_price", None),
                    min_bedrooms=getattr(profile, "min_bedrooms", None),
                    min_area_m2=getattr(profile, "min_area_m2", None),
                    page_withdrawn_signal=bool(portal_result.get("withdrawn_signal")),
                )
                evaluated.append(gate.to_dict())

        qg = summarize_quality_gate(evaluated)

        raw_response = {
            "version": "sooi_v2_6",
            "sources": sources,
            "quality_gate": qg,
            "results": evaluated,
        }

        payload = {
            "write_db": bool(options["commit"]),
            "profile": {
                "id": profile.id,
                "name": profile.name,
                "owner": getattr(profile.owner, "username", str(profile.owner)),
            },
            "municipality": municipality,
            "urls": urls,
            "sources": sources,
            "quality_gate": qg,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "run_id": None,
        }

        if not options["commit"]:
            payload["sample_results"] = evaluated[:20]
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        now = timezone.now()

        with transaction.atomic():
            run = SearchRun.objects.create(
                search_profile=profile,
                status=SearchRun.Status.RUNNING,
                execution_mode=SearchRun.ExecutionMode.PORTAL,
                provider="deterministic_v26",
                model_name="habitaclia_fotocasa_quality_gate_v26",
                query_text=f"SOOI V2.6 deterministic discovery · {municipality}",
                filters_snapshot={
                    "municipality": municipality,
                    "operation_type": profile.operation_type,
                    "province": profile.province,
                    "zone": profile.zone,
                    "property_types": profile.property_types,
                    "min_price": str(profile.min_price) if profile.min_price is not None else None,
                    "max_price": str(profile.max_price) if profile.max_price is not None else None,
                    "min_bedrooms": profile.min_bedrooms,
                    "min_area_m2": str(profile.min_area_m2) if profile.min_area_m2 is not None else None,
                },
                raw_response=raw_response,
                warnings=warnings,
                started_at=now,
            )

            for item in evaluated:
                decision = item.get("decision")
                if decision not in {"verified", "reviewable"}:
                    payload["skipped"] += 1
                    continue

                candidate = item.get("candidate") or {}
                portal = candidate.get("portal")
                source = source_by_portal.get(portal)

                if source is None:
                    payload["skipped"] += 1
                    continue

                source_url = candidate.get("source_url") or ""
                existing = CapturedProperty.objects.filter(
                    owner=profile.owner,
                    source_url=source_url,
                ).order_by("id").first()

                defaults = {
                    "search_profile": profile,
                    "search_run": run,
                    "source": source,
                    "entry_mode": CapturedProperty.EntryMode.AI_EXPLORATION,
                    "operation_type": profile.operation_type,
                    "owner": profile.owner,
                    "source_url": source_url,
                    "source_external_id": external_id_from_url(portal, source_url),
                    "title": (candidate.get("title") or source_url)[:255],
                    "description_raw": candidate.get("evidence") or "",
                    "province": profile.province or "",
                    "municipality": municipality,
                    "zone_text": profile.zone or municipality,
                    "property_type": property_type_from_profile(profile),
                    "price": decimal_or_none(candidate.get("price")),
                    "bedrooms": candidate.get("bedrooms"),
                    "area_m2": decimal_or_none(candidate.get("area_m2")),
                    "status": captured_status_for_gate(decision),
                    "review_status": CapturedProperty.ReviewStatus.PENDING,
                    "ai_summary": f"SOOI V2.6 {decision}: {item.get('reason')}",
                    "ai_signals": {
                        "sooi_v26": True,
                        "decision": decision,
                        "reason": item.get("reason"),
                        "warnings": item.get("warnings") or [],
                        "source_provider": candidate.get("source_provider"),
                        "portal": portal,
                    },
                    "last_seen_at": now,
                }

                if existing:
                    for key, value in defaults.items():
                        setattr(existing, key, value)
                    existing.save()
                    payload["updated"] += 1
                else:
                    CapturedProperty.objects.create(**defaults)
                    payload["created"] += 1

            run.status = SearchRun.Status.COMPLETED
            run.finished_at = timezone.now()
            run.raw_response = raw_response
            run.total_candidates = qg["total_candidates"]
            run.total_valid_candidates = qg["total_valid_candidates"]
            run.total_found = qg["total_found"]
            run.total_new = payload["created"]
            run.total_updated = payload["updated"]
            run.total_errors = qg["total_errors"]
            run.save()

            payload["run_id"] = run.id

        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
