from __future__ import annotations

import json
from django.core.management.base import BaseCommand

from apps.busquedas.services_portal_extractors import probe_portal_url
from apps.busquedas.services_quality_gate_v26 import (
    evaluate_candidate_v26,
    summarize_quality_gate,
)


class Command(BaseCommand):
    help = "SOOI V2.6: Quality Gate dry-run sobre extractores deterministas, sin escribir BD."

    def add_arguments(self, parser):
        parser.add_argument("--municipality", required=True)
        parser.add_argument("--min-price", default=None)
        parser.add_argument("--max-price", default=None)
        parser.add_argument("--min-bedrooms", default=None)
        parser.add_argument("--min-area-m2", default=None)
        parser.add_argument("--timeout", type=int, default=25)
        parser.add_argument("--habitaclia-url", default=None)
        parser.add_argument("--fotocasa-url", default=None)

    def handle(self, *args, **options):
        portal_results = []

        if options.get("habitaclia_url"):
            portal_results.append(
                probe_portal_url(
                    "habitaclia",
                    options["habitaclia_url"],
                    municipality=options["municipality"],
                    timeout=options["timeout"],
                )
            )

        if options.get("fotocasa_url"):
            portal_results.append(
                probe_portal_url(
                    "fotocasa",
                    options["fotocasa_url"],
                    municipality=options["municipality"],
                    timeout=options["timeout"],
                )
            )

        evaluated = []
        sources = []

        for portal_result in portal_results:
            sources.append({
                "portal": portal_result.get("portal"),
                "source_provider": f"deterministic_{portal_result.get('portal')}",
                "http_status": portal_result.get("http_status"),
                "candidate_count": portal_result.get("candidate_count"),
                "withdrawn_signal": portal_result.get("withdrawn_signal"),
            })

            for candidate in portal_result.get("candidates", []):
                gate = evaluate_candidate_v26(
                    candidate,
                    municipality=options["municipality"],
                    min_price=options.get("min_price"),
                    max_price=options.get("max_price"),
                    min_bedrooms=options.get("min_bedrooms"),
                    min_area_m2=options.get("min_area_m2"),
                    page_withdrawn_signal=bool(portal_result.get("withdrawn_signal")),
                )
                evaluated.append(gate.to_dict())

        payload = {
            "write_db": False,
            "test": "sooi_v26_quality_gate_dry_run",
            "filters": {
                "municipality": options["municipality"],
                "min_price": options.get("min_price"),
                "max_price": options.get("max_price"),
                "min_bedrooms": options.get("min_bedrooms"),
                "min_area_m2": options.get("min_area_m2"),
            },
            "sources": sources,
            "quality_gate": summarize_quality_gate(evaluated),
            "results": evaluated,
        }

        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
