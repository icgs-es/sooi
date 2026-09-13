from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.busquedas.geography_registry.importer import write_registry


class Command(BaseCommand):
    help = "Build the versioned Spain geography registry from official INE files."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--output", default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if options["dry_run"] and options["apply"]:
            raise CommandError("--dry-run and --apply are mutually exclusive")
        source = Path(options["source"])
        output = Path(options["output"] or "apps/busquedas/geography_registry/data/v2")
        try:
            manifest = write_registry(source, output, options["version"], apply=options["apply"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        mode = "applied" if options["apply"] else "validated (dry-run)"
        self.stdout.write(self.style.SUCCESS(
            f"{mode}: {manifest['registry_version']} municipalities={manifest['entity_counts']['municipality']}"
        ))
