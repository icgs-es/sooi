from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "busquedas",
            "0012_searchrun_reuse_provenance_sr03a4",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="searchprofile",
            name="geography_bridge_snapshot",
            field=models.JSONField(
                blank=True,
                null=True,
                verbose_name=(
                    "snapshot del puente geográfico"
                ),
            ),
        ),
    ]
