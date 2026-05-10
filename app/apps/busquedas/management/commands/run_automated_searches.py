from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.busquedas.tasks import run_search_profile_task


class Command(BaseCommand):
    help = "Ejecuta búsquedas automatizadas activadas de forma controlada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Ejecuta realmente. Si no se indica, solo simula.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=6,
            help="Máximo de búsquedas a procesar.",
        )
        parser.add_argument(
            "--search-profile-id",
            type=int,
            default=None,
            help="Ejecuta solo una búsqueda concreta.",
        )
        parser.add_argument(
            "--min-hours",
            type=int,
            default=24,
            help="Horas mínimas entre ejecuciones automáticas de la misma búsqueda.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignora el enfriamiento y permite ejecutar aunque haya una ejecución reciente.",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        limit = options["limit"]
        search_profile_id = options["search_profile_id"]
        min_hours = options["min_hours"]
        force = options["force"]

        qs = SearchProfile.objects.filter(
            automation_enabled=True,
            status=SearchProfile.Status.ACTIVE,
        ).select_related("owner").order_by("updated_at", "id")

        if search_profile_id:
            qs = qs.filter(id=search_profile_id)

        qs = qs[:limit]

        self.stdout.write("")
        self.stdout.write("=== AUTOMATIZACIÓN CONTROLADA DE BÚSQUEDAS ===")
        self.stdout.write(f"Modo: {'EJECUCIÓN REAL' if execute else 'SIMULACIÓN'}")
        self.stdout.write(f"Límite: {limit}")
        self.stdout.write(f"Enfriamiento: {min_hours} horas")
        self.stdout.write(f"Forzar: {'sí' if force else 'no'}")

        total_candidates = 0
        total_enqueued = 0
        total_skipped = 0

        for search in qs:
            total_candidates += 1

            running_exists = SearchRun.objects.filter(
                search_profile=search,
                status__in=[SearchRun.Status.PENDING, SearchRun.Status.RUNNING],
            ).exists()

            self.stdout.write("")
            self.stdout.write(f"Búsqueda #{search.id}: {search.name}")
            self.stdout.write(f"Usuario: {search.owner}")
            self.stdout.write(f"Operación: {search.get_operation_type_display()}")
            self.stdout.write(f"Provincia: {search.province}")
            self.stdout.write(f"Zona: {search.zone or '—'}")

            if running_exists:
                total_skipped += 1
                self.stdout.write(self.style.WARNING("OMITIDA: ya tiene una ejecución pendiente o en curso."))
                continue

            recent_cutoff = timezone.now() - timedelta(hours=min_hours)
            recent_run = (
                SearchRun.objects.filter(
                    search_profile=search,
                    execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                    created_at__gte=recent_cutoff,
                )
                .exclude(status__in=[SearchRun.Status.PENDING, SearchRun.Status.RUNNING])
                .order_by("-created_at")
                .first()
            )

            if recent_run and not force:
                total_skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"OMITIDA: ejecución reciente #{recent_run.id} dentro de las últimas {min_hours} horas."
                    )
                )
                continue

            if not execute:
                self.stdout.write(self.style.WARNING("SIMULACIÓN: se ejecutaría esta búsqueda."))
                continue

            run = SearchRun.objects.create(
                search_profile=search,
                status=SearchRun.Status.PENDING,
                execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                provider="automation",
                model_name="scheduled",
                started_at=timezone.now(),
                run_notes="Ejecución automatizada controlada.",
                filters_snapshot={
                    "operation_type": search.operation_type,
                    "province": search.province,
                    "zone": search.zone or "",
                    "property_types": search.property_types or [],
                    "min_price": str(search.min_price) if search.min_price is not None else None,
                    "max_price": str(search.max_price) if search.max_price is not None else None,
                    "min_area_m2": str(search.min_area_m2) if search.min_area_m2 is not None else None,
                    "min_bedrooms": search.min_bedrooms,
                    "automation_enabled": search.automation_enabled,
                    "trigger": "run_automated_searches",
                },
            )

            run_search_profile_task.delay(search.id, run.id)
            total_enqueued += 1

            self.stdout.write(self.style.SUCCESS(f"LANZADA: SearchRun #{run.id}"))

        self.stdout.write("")
        self.stdout.write("=== RESUMEN ===")
        self.stdout.write(f"Candidatas: {total_candidates}")
        self.stdout.write(f"Lanzadas: {total_enqueued}")
        self.stdout.write(f"Omitidas: {total_skipped}")

        if not execute:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("No se ha ejecutado nada. Usa --execute para lanzar búsquedas reales."))
