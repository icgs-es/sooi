from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "inmuebles",
            "0007_capturedproperty_availability_verification",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="capturedproperty",
            name="geo_canonical_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=128,
                null=True,
                verbose_name=(
                    "clave geográfica canónica"
                ),
            ),
        ),
        migrations.AddField(
            model_name="capturedproperty",
            name="geo_resolution_status",
            field=models.CharField(
                blank=True,
                max_length=32,
                null=True,
                verbose_name=(
                    "estado de resolución geográfica"
                ),
            ),
        ),
        migrations.AddField(
            model_name="capturedproperty",
            name="geo_resolution_method",
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                verbose_name=(
                    "método de resolución geográfica"
                ),
            ),
        ),
        migrations.AddField(
            model_name="capturedproperty",
            name="geo_registry_version",
            field=models.CharField(
                blank=True,
                max_length=128,
                null=True,
                verbose_name=(
                    "versión del registro geográfico"
                ),
            ),
        ),
        migrations.AddField(
            model_name="capturedproperty",
            name="geo_registry_digest",
            field=models.CharField(
                blank=True,
                max_length=80,
                null=True,
                verbose_name=(
                    "digest del registro geográfico"
                ),
            ),
        ),
    ]
