"""Idempotent replay of persisted review candidates into the capture inbox.

This command deliberately consumes only a persisted SearchRun payload.  It
does not execute discovery, call providers, spend credits, or create a run.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.busquedas.models import SearchRun
from apps.busquedas.services_hybrid_coverage_v261 import (
    _apply_action_plan_to_db,
    _build_action_plan,
)


class Command(BaseCommand):
    help = "Reproduce el paso de captación desde un SearchRun persistido (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", type=int, required=True)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="No escribe (opción por defecto).")
        mode.add_argument("--apply", action="store_true", help="Escribe captaciones de revisión de forma idempotente.")

    def handle(self, *args, **options):
        try:
            run = SearchRun.objects.select_related("search_profile").get(pk=options["run_id"])
        except SearchRun.DoesNotExist as exc:
            raise CommandError(f"SearchRun no encontrado: {options['run_id']}") from exc

        payload = run.raw_response if isinstance(run.raw_response, dict) else {}
        rows = payload.get("source_coverage")
        if not isinstance(rows, list):
            raise CommandError("El SearchRun no contiene source_coverage persistida.")

        plan = _build_action_plan(rows)
        totals = plan.get("totals") or {}
        review_candidates = sum(
            1 for action in plan.get("actions", [])
            if action.get("classification") == "reviewable"
        )
        already_captured = int(totals.get("skip_duplicate") or 0)
        rejected = int(totals.get("skip_discarded") or 0)
        would_create = int(totals.get("would_create_captured") or 0) + int(
            totals.get("would_create_in_review") or 0
        )

        result = {
            "RUN_ID": run.pk,
            "REVIEW_CANDIDATES": review_candidates,
            "ALREADY_CAPTURED": already_captured,
            "WOULD_CREATE": would_create,
            "WOULD_UPDATE": int(totals.get("would_mark_existing_discarded") or 0),
            "WOULD_SKIP": int(totals.get("skip_unknown") or 0),
            "REJECTED": rejected,
            "DUPLICATES": already_captured,
            "ERRORS": 0,
            "MODE": "apply" if options.get("apply") else "dry-run",
        }

        if options.get("apply"):
            write_result = _apply_action_plan_to_db(
                run.search_profile,
                payload.get("context") or {},
                rows,
                plan,
                search_run=run,
            )
            result.update({
                "CREATED": write_result.get("created", 0),
                "CREATED_IN_REVIEW": write_result.get("created_in_review", 0),
                "UPDATED": write_result.get("updated", 0),
                "SKIPPED": write_result.get("skipped", 0),
            })

        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
