from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("busquedas", "0011_searchrun_governance_sr03a2")]
    operations = [
        migrations.AddField(
            model_name="searchrun",
            name="reused_from_search_run",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=models.SET_NULL,
                related_name="reuse_runs", to="busquedas.searchrun",
            ),
        ),
    ]
