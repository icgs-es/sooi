from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun


class Command(BaseCommand):
    help = "Audita el estado de las búsquedas con automatización."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Muestra todas las búsquedas, no solo las automatizadas.",
        )
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Ventana de cooldown en horas.",
        )

    def handle(self, *args, **options):
        show_all = options["all"]
        hours = options["hours"]
        now = timezone.now()
        cutoff = now - timedelta(hours=hours)

        qs = SearchProfile.objects.select_related("owner").order_by("owner__username", "id")

        if not show_all:
            qs = qs.filter(automation_enabled=True)

        self.stdout.write("")
        self.stdout.write("=== AUDITORÍA AUTOMATIZACIÓN DE BÚSQUEDAS ===")
        self.stdout.write(f"Modo: {'todas' if show_all else 'solo automatizadas'}")
        self.stdout.write(f"Cooldown evaluado: {hours} horas")
        self.stdout.write("")

        total = 0
        enabled = 0
        runnable = 0
        blocked = 0

        for search in qs:
            total += 1
            if search.automation_enabled:
                enabled += 1

            last_run = SearchRun.objects.filter(search_profile=search).order_by("-created_at").first()
            recent_run = (
                SearchRun.objects.filter(
                    search_profile=search,
                    execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                    created_at__gte=cutoff,
                )
                .exclude(status__in=[SearchRun.Status.PENDING, SearchRun.Status.RUNNING])
                .order_by("-created_at")
                .first()
            )

            pending_or_running = SearchRun.objects.filter(
                search_profile=search,
                status__in=[SearchRun.Status.PENDING, SearchRun.Status.RUNNING],
            ).order_by("-created_at").first()

            can_run = (
                search.automation_enabled
                and search.status == SearchProfile.Status.ACTIVE
                and not recent_run
                and not pending_or_running
            )

            if can_run:
                runnable += 1
                availability = "EJECUTABLE"
            elif not search.automation_enabled:
                availability = "NO AUTOMATIZADA"
            elif search.status != SearchProfile.Status.ACTIVE:
                availability = f"NO ACTIVA ({search.status})"
            elif pending_or_running:
                blocked += 1
                availability = f"BLOQUEADA: run {pending_or_running.id} {pending_or_running.status}"
            elif recent_run:
                blocked += 1
                availability = f"BLOQUEADA: cooldown por run {recent_run.id}"
            else:
                availability = "NO EJECUTABLE"

            owner = getattr(search.owner, "username", None) or str(search.owner)
            zone = search.zone or "—"

            self.stdout.write(f"#{search.id} · {search.name}")
            self.stdout.write(f"  Owner: {owner}")
            self.stdout.write(f"  Estado: {search.status} · Automatización: {search.automation_enabled}")
            self.stdout.write(f"  Operación: {search.operation_type} · Provincia: {search.province} · Zona: {zone}")
            self.stdout.write(f"  Disponibilidad: {availability}")

            if last_run:
                self.stdout.write(
                    f"  Último run: #{last_run.id} · {last_run.status} · "
                    f"new={last_run.total_new} · updated={last_run.total_updated} · "
                    f"errors={last_run.total_errors} · created={last_run.created_at}"
                )
            else:
                self.stdout.write("  Último run: —")

            self.stdout.write("")

        self.stdout.write("=== RESUMEN ===")
        self.stdout.write(f"Búsquedas listadas: {total}")
        self.stdout.write(f"Automatización activada: {enabled}")
        self.stdout.write(f"Ejecutables ahora: {runnable}")
        self.stdout.write(f"Bloqueadas por cooldown/ejecución: {blocked}")
