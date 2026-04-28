from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Elimina datos operativos de prueba de SOOI "
        "(busquedas, captaciones, oportunidades y seguimiento ligado), "
        "sin tocar catalogos base."
    )

    # Ordenado de hijo -> padre
    MODEL_SPECS = [
        ("seguimiento", "Alert"),
        ("seguimiento", "FollowUpTask"),
        ("inmuebles", "OpportunityContact"),
        ("inmuebles", "PropertyOpportunity"),
        ("inmuebles", "CapturedProperty"),
        ("busquedas", "SearchRun"),
        ("busquedas", "SearchProfile"),
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra lo que se borraria sin ejecutar cambios.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        resolved = []
        total_objects = 0

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Reset operativo SOOI"))
        self.stdout.write("")

        for app_label, model_name in self.MODEL_SPECS:
            model = self._get_model(app_label, model_name)
            if not model:
                self.stdout.write(
                    self.style.WARNING(
                        f"[SKIP] Modelo no encontrado: {app_label}.{model_name}"
                    )
                )
                continue

            qs = model.objects.all()
            count = qs.count()
            total_objects += count

            resolved.append(
                {
                    "app_label": app_label,
                    "model_name": model_name,
                    "model": model,
                    "count": count,
                }
            )

            self.stdout.write(f"{app_label}.{model_name}: {count}")

        self.stdout.write("")
        self.stdout.write(f"Total objetos afectados: {total_objects}")
        self.stdout.write(
            "Catalogos preservados: Source, BrokerCompany y configuracion base."
        )
        self.stdout.write(f"Modo: {'DRY-RUN' if dry_run else 'EJECUCION REAL'}")
        self.stdout.write("")

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS("Dry-run completado. No se ha borrado nada.")
            )
            return

        deleted_total = 0

        with transaction.atomic():
            for item in resolved:
                model = item["model"]
                deleted_count, _detail = model.objects.all().delete()
                deleted_total += deleted_count

                self.stdout.write(
                    self.style.WARNING(
                        f"[DELETE] {item['app_label']}.{item['model_name']}: {deleted_count}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Reset completado correctamente."))
        self.stdout.write(f"Total eliminado reportado por Django: {deleted_total}")
        self.stdout.write("")

    def _get_model(self, app_label, model_name):
        try:
            return apps.get_model(app_label, model_name)
        except LookupError:
            return None