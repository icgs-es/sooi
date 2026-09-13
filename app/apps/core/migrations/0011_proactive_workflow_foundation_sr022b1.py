import datetime

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0010_commercial_metering_sr03c"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("daily_digest_enabled", models.BooleanField(default=False, verbose_name="resumen diario")),
                ("daily_digest_time", models.TimeField(default=datetime.time(8, 0), verbose_name="hora del resumen")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="creado")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="actualizado")),
                ("owner", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="notification_preference", to=settings.AUTH_USER_MODEL, verbose_name="propietario")),
            ],
            options={
                "verbose_name": "Preferencia de notificación",
                "verbose_name_plural": "Preferencias de notificación",
            },
        ),
        migrations.CreateModel(
            name="DailyDigestDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("delivery_type", models.CharField(choices=[("daily_digest", "Resumen diario")], default="daily_digest", max_length=30, verbose_name="tipo de entrega")),
                ("channel", models.CharField(choices=[("email", "Correo electrónico")], default="email", max_length=20, verbose_name="canal")),
                ("local_date", models.DateField(verbose_name="fecha local")),
                ("status", models.CharField(choices=[("scheduled", "Programado"), ("processing", "Procesando"), ("sent", "Enviado"), ("failed", "Fallido"), ("skipped", "Omitido"), ("unknown", "Desconocido")], db_index=True, default="scheduled", max_length=20, verbose_name="estado")),
                ("scheduled_for", models.DateTimeField(blank=True, null=True, verbose_name="programado para")),
                ("attempted_at", models.DateTimeField(blank=True, null=True, verbose_name="intentado en")),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="enviado en")),
                ("attempt_count", models.PositiveSmallIntegerField(default=0, verbose_name="número de intentos")),
                ("next_retry_at", models.DateTimeField(blank=True, null=True, verbose_name="próximo reintento")),
                ("reason_code", models.CharField(blank=True, max_length=80, verbose_name="código de motivo")),
                ("last_error_class", models.CharField(blank=True, max_length=120, verbose_name="clase del último error")),
                ("payload_fingerprint", models.CharField(blank=True, max_length=64, verbose_name="huella del contenido")),
                ("claim_token", models.UUIDField(blank=True, editable=False, null=True, verbose_name="token de asignación")),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True, verbose_name="fin de asignación")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="creado")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="actualizado")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="daily_digest_deliveries", to=settings.AUTH_USER_MODEL, verbose_name="propietario")),
            ],
            options={
                "verbose_name": "Entrega de resumen diario",
                "verbose_name_plural": "Entregas de resumen diario",
                "indexes": [
                    models.Index(fields=["status", "local_date"], name="core_digest_status_date"),
                    models.Index(fields=["owner", "local_date"], name="core_digest_owner_date"),
                    models.Index(fields=["status", "next_retry_at"], name="core_digest_retry"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("owner", "delivery_type", "channel", "local_date"), name="core_daily_digest_delivery_identity"),
                ],
            },
        ),
    ]
