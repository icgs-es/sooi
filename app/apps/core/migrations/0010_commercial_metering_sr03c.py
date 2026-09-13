from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0009_dailyproductmetric"),
        ("busquedas", "0012_searchrun_reuse_provenance_sr03a4"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchCreditAccountPeriod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("allowance_credits", models.DecimalField(decimal_places=2, max_digits=12)),
                ("plan_code", models.CharField(max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="search_credit_periods", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="SearchCreditLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("idempotency_key", models.CharField(max_length=160, unique=True)),
                ("entry_type", models.CharField(choices=[("reservation", "Reserva"), ("settlement", "Consumo"), ("release", "Liberación"), ("adjustment", "Ajuste")], max_length=20)),
                ("amount_credits", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.CharField(blank=True, default="", max_length=200)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="core.searchcreditaccountperiod")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="search_credit_ledger_entries", to=settings.AUTH_USER_MODEL)),
                ("search_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commercial_credit_entries", to="busquedas.searchrun")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddConstraint(model_name="searchcreditaccountperiod", constraint=models.UniqueConstraint(fields=("owner", "period_start"), name="search_credit_owner_period_unique")),
        migrations.AddConstraint(model_name="searchcreditaccountperiod", constraint=models.CheckConstraint(condition=models.Q(("period_end__gt", models.F("period_start"))), name="search_credit_period_dates_valid")),
        migrations.AddConstraint(model_name="searchcreditaccountperiod", constraint=models.CheckConstraint(condition=models.Q(("allowance_credits__gte", 0)), name="search_credit_allowance_nonnegative")),
        migrations.AddConstraint(model_name="searchcreditledgerentry", constraint=models.CheckConstraint(condition=models.Q(("entry_type", "adjustment"), ("amount_credits__gte", 0), _connector="OR"), name="search_credit_direction_valid")),
    ]
