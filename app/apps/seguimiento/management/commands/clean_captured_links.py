from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity
from apps.seguimiento.services_availability import (
    AVAILABLE,
    NOT_VERIFIABLE,
    TEMPORARY_ERROR,
    UNAVAILABLE,
    apply_captured_property_cleanup,
    check_captured_property_availability,
)


class Command(BaseCommand):
    help = "Audita URLs origen de captaciones y descarta técnicamente las no disponibles solo si hay evidencia clara."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="", help="Filtro por fuente en source_url. Ej: idealista")
        parser.add_argument("--days", type=int, default=30, help="Revisar captaciones de los últimos N días")
        parser.add_argument("--limit", type=int, default=50, help="Límite máximo de captaciones a revisar")
        parser.add_argument("--owner-id", type=int, default=None)
        parser.add_argument("--search-profile-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true", help="No modifica datos")
        parser.add_argument("--commit", action="store_true", help="Aplica cambios")
        parser.add_argument("--verbose", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        commit = options["commit"]

        if dry_run and commit:
            raise CommandError("Usa --dry-run o --commit, no ambos.")

        if not dry_run and not commit:
            dry_run = True

        source = (options["source"] or "").strip()
        days = options["days"]
        limit = options["limit"]

        qs = (
            CapturedProperty.objects
            .select_related("source", "search_profile", "owner")
            .filter(status=CapturedProperty.Status.CAPTURED)
            .exclude(source_url="")
            .order_by("-captured_at")
        )

        if source:
            qs = qs.filter(source_url__icontains=source)

        if days:
            since = timezone.now() - timedelta(days=days)
            qs = qs.filter(captured_at__gte=since)

        if options["owner_id"]:
            qs = qs.filter(owner_id=options["owner_id"])

        if options["search_profile_id"]:
            qs = qs.filter(search_profile_id=options["search_profile_id"])

        converted_ids = (
            PropertyOpportunity.objects
            .exclude(captured_property_id__isnull=True)
            .values_list("captured_property_id", flat=True)
        )
        qs = qs.exclude(id__in=converted_ids)

        total_candidates = qs.count()
        items = list(qs[:limit])

        summary = {
            "mode": "dry-run" if dry_run else "commit",
            "candidates_total": total_candidates,
            "reviewed": 0,
            AVAILABLE: 0,
            UNAVAILABLE: 0,
            NOT_VERIFIABLE: 0,
            TEMPORARY_ERROR: 0,
            "would_discard": 0,
            "discarded": 0,
            "no_action": 0,
        }

        self.stdout.write("=== SOOI V2.1 CLEAN CAPTURED LINKS ===")
        self.stdout.write(f"MODE={summary['mode']}")
        self.stdout.write(f"SOURCE={source or 'all'}")
        self.stdout.write(f"DAYS={days}")
        self.stdout.write(f"LIMIT={limit}")
        self.stdout.write(f"CANDIDATES_TOTAL={total_candidates}")
        self.stdout.write("")

        for obj in items:
            summary["reviewed"] += 1

            result = check_captured_property_availability(obj)
            summary[result.availability] += 1

            action = apply_captured_property_cleanup(obj, result, commit=commit)
            summary[action] = summary.get(action, 0) + 1

            if options["verbose"] or result.availability != AVAILABLE:
                self.stdout.write(
                    f"id={obj.id} | status={obj.status} | "
                    f"availability={result.availability} | "
                    f"action={action} | http={result.status_code} | "
                    f"reason={result.reason} | url={obj.source_url}"
                )

        self.stdout.write("")
        self.stdout.write("=== SUMMARY ===")
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("DRY-RUN: no se han modificado datos."))
