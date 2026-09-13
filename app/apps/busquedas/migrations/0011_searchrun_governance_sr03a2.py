from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("busquedas", "0010_geographicarea_searchprofile_geography")]

    operations = [
        migrations.AddField(model_name="searchrun", name="actual_internal_cost", field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
        migrations.AddField(model_name="searchrun", name="budget_consumed_credits", field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
        migrations.AddField(model_name="searchrun", name="budget_max_credits", field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
        migrations.AddField(model_name="searchrun", name="budget_reserved_credits", field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
        migrations.AddField(model_name="searchrun", name="cache_hits", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="calls_attempted", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="calls_planned", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="calls_succeeded", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="coverage_status", field=models.CharField(choices=[("not_evaluated", "No evaluada / histórica"), ("full", "Completa"), ("partial", "Parcial"), ("limited_by_plan", "Limitada por plan"), ("limited_by_budget", "Limitada por presupuesto"), ("degraded_provider", "Proveedor degradado"), ("no_results", "Sin resultados"), ("failed", "Fallida")], default="not_evaluated", max_length=30)),
        migrations.AddField(model_name="searchrun", name="estimated_internal_cost", field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
        migrations.AddField(model_name="searchrun", name="governance_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="searchrun", name="governance_version", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="searchrun", name="search_fingerprint", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="searchrun", name="search_mode", field=models.CharField(choices=[("legacy", "No gobernada (histórica)"), ("free", "Free"), ("eco", "Eco"), ("amplia", "Amplia"), ("profunda", "Profunda")], default="legacy", max_length=20)),
        migrations.AddField(model_name="searchrun", name="sources_executed", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="sources_omitted", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="sources_planned", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="searchrun", name="stop_reason", field=models.CharField(choices=[("none", "Sin motivo / no establecido"), ("sufficient_coverage", "Cobertura suficiente"), ("budget_exhausted", "Presupuesto agotado"), ("provider_hard_failure", "Fallo duro del proveedor"), ("provider_outage", "Caída del proveedor"), ("no_applicable_sources", "Sin fuentes aplicables"), ("plan_limit", "Límite del plan"), ("user_cancelled", "Cancelada por el usuario"), ("completed_plan", "Plan completado"), ("failed_internal", "Fallo interno")], default="none", max_length=30)),
        migrations.AddConstraint(model_name="searchrun", constraint=models.CheckConstraint(condition=models.Q(("governance_enabled", False), ("budget_max_credits__isnull", True), models.Q(("budget_reserved_credits__isnull", False), ("budget_reserved_credits__lte", models.F("budget_max_credits")), _connector="AND"), _connector="OR"), name="searchrun_reserved_lte_max")),
        migrations.AddConstraint(model_name="searchrun", constraint=models.CheckConstraint(condition=models.Q(("governance_enabled", False), ("budget_max_credits__isnull", True), models.Q(("budget_reserved_credits__isnull", False), ("budget_consumed_credits__isnull", False), ("budget_consumed_credits__lte", models.F("budget_reserved_credits")), _connector="AND"), _connector="OR"), name="searchrun_consumed_lte_reserved")),
    ]
