from __future__ import annotations

import json
from django.core.management.base import BaseCommand

from apps.busquedas.services_portal_extractors import probe_portal_url


class Command(BaseCommand):
    help = "SOOI V2.6: prueba extractores deterministas sin escribir BD."

    def add_arguments(self, parser):
        parser.add_argument("--municipality", default=None)
        parser.add_argument("--timeout", type=int, default=25)
        parser.add_argument("--habitaclia-url", default=None)
        parser.add_argument("--fotocasa-url", default=None)

    def handle(self, *args, **options):
        results = []

        if options.get("habitaclia_url"):
            results.append(
                probe_portal_url(
                    "habitaclia",
                    options["habitaclia_url"],
                    municipality=options.get("municipality"),
                    timeout=options["timeout"],
                )
            )

        if options.get("fotocasa_url"):
            results.append(
                probe_portal_url(
                    "fotocasa",
                    options["fotocasa_url"],
                    municipality=options.get("municipality"),
                    timeout=options["timeout"],
                )
            )

        payload = {
            "write_db": False,
            "test": "sooi_v26_deterministic_extractors",
            "municipality": options.get("municipality"),
            "results": results,
        }

        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
