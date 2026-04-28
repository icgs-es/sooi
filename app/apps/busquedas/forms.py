from django import forms

from .models import SearchProfile


SPAIN_PROVINCE_CHOICES = [
    ("", "Selecciona provincia"),
    ("A Coruña", "A Coruña"),
    ("Álava", "Álava"),
    ("Albacete", "Albacete"),
    ("Alicante", "Alicante"),
    ("Almería", "Almería"),
    ("Asturias", "Asturias"),
    ("Ávila", "Ávila"),
    ("Badajoz", "Badajoz"),
    ("Barcelona", "Barcelona"),
    ("Burgos", "Burgos"),
    ("Cáceres", "Cáceres"),
    ("Cádiz", "Cádiz"),
    ("Cantabria", "Cantabria"),
    ("Castellón", "Castellón"),
    ("Ciudad Real", "Ciudad Real"),
    ("Córdoba", "Córdoba"),
    ("Cuenca", "Cuenca"),
    ("Girona", "Girona"),
    ("Granada", "Granada"),
    ("Guadalajara", "Guadalajara"),
    ("Gipuzkoa", "Gipuzkoa"),
    ("Huelva", "Huelva"),
    ("Huesca", "Huesca"),
    ("Illes Balears", "Illes Balears"),
    ("Jaén", "Jaén"),
    ("León", "León"),
    ("Lleida", "Lleida"),
    ("Lugo", "Lugo"),
    ("Madrid", "Madrid"),
    ("Málaga", "Málaga"),
    ("Murcia", "Murcia"),
    ("Navarra", "Navarra"),
    ("Ourense", "Ourense"),
    ("Palencia", "Palencia"),
    ("Las Palmas", "Las Palmas"),
    ("Pontevedra", "Pontevedra"),
    ("La Rioja", "La Rioja"),
    ("Salamanca", "Salamanca"),
    ("Santa Cruz de Tenerife", "Santa Cruz de Tenerife"),
    ("Segovia", "Segovia"),
    ("Sevilla", "Sevilla"),
    ("Soria", "Soria"),
    ("Tarragona", "Tarragona"),
    ("Teruel", "Teruel"),
    ("Toledo", "Toledo"),
    ("Valencia", "Valencia"),
    ("Valladolid", "Valladolid"),
    ("Bizkaia", "Bizkaia"),
    ("Zamora", "Zamora"),
    ("Zaragoza", "Zaragoza"),
    ("Ceuta", "Ceuta"),
    ("Melilla", "Melilla"),
]


class SearchProfileForm(forms.ModelForm):
    province = forms.ChoiceField(
        label="Provincia",
        choices=SPAIN_PROVINCE_CHOICES,
        required=True,
    )

    property_types = forms.MultipleChoiceField(
        label="Tipos de propiedad",
        choices=SearchProfile.PropertyType.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = SearchProfile
        fields = [
            "name",
            "operation_type",
            "province",
            "zone",
            "property_types",
            "max_price",
            "min_bedrooms",
            "ai_prompt",
            "automation_enabled",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ej. Córdoba inversión"}),
            "zone": forms.TextInput(
                attrs={"placeholder": "Ej. Marbella, Estepona, Nueva Andalucía, centro..."}
            ),
            "max_price": forms.NumberInput(attrs={"step": "0.01"}),
            "min_bedrooms": forms.NumberInput(attrs={"min": 0}),
            "ai_prompt": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Ej. Prioriza inmuebles con descuento, necesidad de reforma o rentabilidad.",
                }
            ),
            "automation_enabled": forms.CheckboxInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["property_types"].initial = self.instance.property_types or []

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.property_types = self.cleaned_data.get("property_types", [])
        if commit:
            obj.save()
        return obj