from django.db import migrations, models
import django.db.models.deletion


LOS_PEDROCHES = [
    "Pozoblanco", "Alcaracejos", "Añora", "Belalcázar", "Cardeña", "Conquista",
    "Dos Torres", "El Guijo", "El Viso", "Fuente La Lancha", "Hinojosa del Duque",
    "Pedroche", "Santa Eufemia", "Torrecampo", "Villanueva de Córdoba",
    "Villanueva del Duque", "Villaralto",
]


def seed_los_pedroches(apps, schema_editor):
    GeographicArea = apps.get_model("busquedas", "GeographicArea")
    GeographicArea.objects.get_or_create(
        province="Córdoba", name="Los Pedroches",
        defaults={"area_type": "comarca", "municipalities": LOS_PEDROCHES, "is_active": True},
    )


def unseed_los_pedroches(apps, schema_editor):
    apps.get_model("busquedas", "GeographicArea").objects.filter(
        province="Córdoba", name="Los Pedroches"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("busquedas", "0009_searchprofile_min_area_m2_searchprofile_min_price")]
    operations = [
        migrations.CreateModel(
            name="GeographicArea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, verbose_name="nombre")),
                ("area_type", models.CharField(choices=[("comarca", "Comarca"), ("custom", "Zona personalizada")], max_length=20, verbose_name="tipo")),
                ("province", models.CharField(max_length=100, verbose_name="provincia")),
                ("municipalities", models.JSONField(default=list, verbose_name="municipios")),
                ("is_active", models.BooleanField(default=True, verbose_name="activa")),
            ],
            options={"verbose_name": "Área geográfica", "verbose_name_plural": "Áreas geográficas", "ordering": ["province", "name"]},
        ),
        migrations.AddConstraint(
            model_name="geographicarea",
            constraint=models.UniqueConstraint(fields=("province", "name"), name="unique_geographic_area_name_per_province"),
        ),
        migrations.AddField(
            model_name="searchprofile", name="geography_scope",
            field=models.CharField(blank=True, choices=[("municipality", "Un municipio"), ("multi_municipality", "Varios municipios"), ("named_area", "Comarca o zona guardada"), ("province", "Toda la provincia")], help_text="Vacío indica un perfil antiguo que todavía se resuelve desde zona.", max_length=30, verbose_name="ámbito geográfico"),
        ),
        migrations.AddField(
            model_name="searchprofile", name="municipalities",
            field=models.JSONField(blank=True, default=list, verbose_name="municipios seleccionados"),
        ),
        migrations.AddField(
            model_name="searchprofile", name="geographic_area",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="search_profiles", to="busquedas.geographicarea", verbose_name="comarca o zona guardada"),
        ),
        migrations.RunPython(seed_los_pedroches, unseed_los_pedroches),
    ]
