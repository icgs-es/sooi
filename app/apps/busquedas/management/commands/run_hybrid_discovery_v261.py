import json
from django.core.management.base import BaseCommand, CommandError

from apps.busquedas.services_hybrid_coverage_v261 import run_hybrid_discovery_v261


class Command(BaseCommand):
    help = "SOOI V2.6.1 dry-run: cobertura obligatoria multi-fuente sin escribir BD."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True)
        parser.add_argument("--timeout", type=int, default=12)
        parser.add_argument("--max-results-per-source", type=int, default=10)
        parser.add_argument("--location-override", default=None, help="Fuerza localidad/municipio para esta ejecución.")
        parser.add_argument("--no-ai", action="store_true", help="No llama a OpenAI Web Search.")
        parser.add_argument("--write", action="store_true", help="Escribe captaciones según action_plan.")
        parser.add_argument("--confirm-write", action="store_true", help="Confirmación explícita para permitir escritura.")

    def handle(self, *args, **options):
        if options["write"] and not options["confirm_write"]:
            raise CommandError("Para escribir debes usar --write --confirm-write.")

        result = run_hybrid_discovery_v261(
            profile_id=options["profile_id"],
            write=bool(options["write"]),
            timeout=options["timeout"],
            max_results_per_source=options["max_results_per_source"],
            use_ai=not options["no_ai"],
            location_override=options.get("location_override"),
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
