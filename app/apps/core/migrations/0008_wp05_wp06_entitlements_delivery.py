from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_userprofile")]

    operations = [
        migrations.AddField(
            model_name="userprofile", name="plan",
            field=models.CharField(
                choices=[("starter", "Starter"), ("professional", "Professional"), ("business", "Business")],
                default="professional", max_length=20, verbose_name="plan",
            ),
        ),
        migrations.AddField(
            model_name="demorequest", name="delivery_status",
            field=models.CharField(
                choices=[("pending", "Pendiente"), ("sent", "Enviada"), ("failed", "Fallida")],
                default="pending", max_length=20, verbose_name="estado de entrega",
            ),
        ),
        migrations.AddField(
            model_name="demorequest", name="notification_attempts",
            field=models.PositiveIntegerField(default=0, verbose_name="intentos de notificación"),
        ),
        migrations.AddField(
            model_name="demorequest", name="last_notification_error",
            field=models.CharField(blank=True, default="", max_length=500, verbose_name="último error de notificación"),
        ),
        migrations.AddField(
            model_name="demorequest", name="notified_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="notificada"),
        ),
    ]
