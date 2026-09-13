from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import DailyProductMetric
from apps.core.product_metrics import CALCULATION_VERSION, calculate_day, validate_closed_day


class Command(BaseCommand):
    help = "Calculate privacy-bounded WP-09 metrics for closed natural days."

    def add_arguments(self, parser):
        parser.add_argument("--start-date", required=True, type=date.fromisoformat)
        parser.add_argument("--end-date", required=True, type=date.fromisoformat)

    def handle(self, *args, **options):
        if not settings.SOOI_WP09_AGGREGATION_ENABLED:
            raise CommandError("SOOI_WP09_AGGREGATION_ENABLED is disabled")
        start = options["start_date"]
        end = options["end_date"]
        if start > end:
            raise CommandError("start-date must not be after end-date")
        try:
            validate_closed_day(end)
        except ValueError as exc:
            raise CommandError(str(exc)) from None

        day = start
        while day <= end:
            with transaction.atomic():
                rows = calculate_day(day)
                calculated_at = timezone.now()
                list(DailyProductMetric.objects.select_for_update().filter(
                    natural_day=day, calculation_version=CALCULATION_VERSION,
                ).values_list("pk", flat=True))
                DailyProductMetric.objects.filter(
                    natural_day=day, calculation_version=CALCULATION_VERSION,
                ).delete()
                DailyProductMetric.objects.bulk_create([
                    DailyProductMetric(
                        natural_day=day, metric_name=metric, dimension_name=dimension,
                        dimension_value=dimension_value, value=value,
                        calculation_version=CALCULATION_VERSION,
                        calculated_at=calculated_at, is_complete=True,
                    )
                    for metric, dimension, dimension_value, value in rows
                ])
            for metric, dimension, dimension_value, value in rows:
                self.stdout.write(f"{day.isoformat()} {metric} {dimension} {dimension_value} {value}")
            day += timedelta(days=1)
