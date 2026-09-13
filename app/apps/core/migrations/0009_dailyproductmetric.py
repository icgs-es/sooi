from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0008_wp05_wp06_entitlements_delivery")]

    operations = [
        migrations.CreateModel(
            name="DailyProductMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("natural_day", models.DateField()),
                ("metric_name", models.CharField(choices=[
                    ("registration_completed", "registration_completed"),
                    ("first_search_created", "first_search_created"),
                    ("first_capture_available", "first_capture_available"),
                    ("first_opportunity_created", "first_opportunity_created"),
                    ("first_dated_next_action", "first_dated_next_action"),
                    ("canonical_activation_completed", "canonical_activation_completed"),
                    ("trial_expired", "trial_expired"),
                    ("demo_request_pending", "demo_request_pending"),
                    ("demo_request_notified", "demo_request_notified"),
                    ("demo_request_delivery_failed", "demo_request_delivery_failed"),
                ], max_length=40)),
                ("dimension_name", models.CharField(choices=[
                    ("all", "all"), ("signup_source", "signup_source"),
                    ("profile_type", "profile_type"),
                ], max_length=20)),
                ("dimension_value", models.CharField(max_length=50)),
                ("value", models.PositiveBigIntegerField()),
                ("calculation_version", models.PositiveSmallIntegerField()),
                ("calculated_at", models.DateTimeField()),
                ("is_complete", models.BooleanField()),
            ],
            options={
                "indexes": [models.Index(fields=["metric_name", "natural_day"], name="core_daily_metric_read")],
                "constraints": [models.UniqueConstraint(fields=("natural_day", "metric_name", "dimension_name", "dimension_value", "calculation_version"), name="core_daily_metric_unique")],
            },
        ),
    ]
