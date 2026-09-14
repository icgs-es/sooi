from pathlib import Path

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.busquedas.geography_registry.evolution import (
    evolve_registry,
)


class Command(BaseCommand):
    help = (
        "Build immutable Spain geography "
        "registry from base plus official "
        "INE changes."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--base",
            required=True,
        )

        parser.add_argument(
            "--modifications",
            required=True,
        )

        parser.add_argument(
            "--registry-version",
            required=True,
        )

        parser.add_argument(
            "--as-of-date",
            required=True,
        )

        parser.add_argument(
            "--source-reference",
            required=True,
        )

        parser.add_argument(
            "--output",
            required=True,
        )

        parser.add_argument(
            "--expected-change-count",
            type=int,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

        parser.add_argument(
            "--apply",
            action="store_true",
        )

    def handle(
        self,
        *args,
        **options,
    ):
        if (
            options["dry_run"]
            == options["apply"]
        ):
            raise CommandError(
                "choose exactly one of "
                "--dry-run or --apply"
            )

        try:
            manifest = (
                evolve_registry(
                    Path(
                        options[
                            "base"
                        ]
                    ),
                    Path(
                        options[
                            "modifications"
                        ]
                    ),
                    Path(
                        options[
                            "output"
                        ]
                    ),
                    options[
                        "registry_version"
                    ],
                    as_of_date=(
                        options[
                            "as_of_date"
                        ]
                    ),
                    source_reference=(
                        options[
                            "source_reference"
                        ]
                    ),
                    expected_change_count=(
                        options[
                            "expected_change_count"
                        ]
                    ),
                    apply=(
                        options[
                            "apply"
                        ]
                    ),
                )
            )

        except Exception as exc:
            raise CommandError(
                str(exc)
            ) from exc

        mode = (
            "applied"
            if options["apply"]
            else "validated"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: "
                f"{manifest['registry_version']} "
                f"municipalities="
                f"{manifest['entity_counts']['municipality']} "
                f"aliases="
                f"{manifest['alias_count']} "
                f"changes="
                f"{manifest['post_baseline_modifications_applied_count']}"
            )
        )
