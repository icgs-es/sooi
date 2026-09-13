from django import forms

from .models import GeographicArea, SearchProfile
from .price import normalize_euro_price


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


class EuroPriceField(forms.DecimalField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        normalized = normalize_euro_price(value)
        if normalized is None:
            raise forms.ValidationError("Introduce un importe válido en euros.")
        return normalized


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
    geography_scope = forms.ChoiceField(
        label="Ámbito geográfico", choices=SearchProfile.GeographyScope.choices, required=True,
    )
    municipalities = forms.CharField(
        label="Municipios", required=False,
        widget=forms.HiddenInput(),
        help_text="Añade cada municipio por separado.",
    )
    geographic_area = forms.ModelChoiceField(
        label="Comarca o zona guardada", queryset=GeographicArea.objects.none(), required=False,
        empty_label="Selecciona una comarca o zona",
    )
    min_price = EuroPriceField(
        label="Precio mínimo", required=False, max_digits=12, decimal_places=2,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej. 60000"}),
    )
    max_price = EuroPriceField(
        label="Precio máximo", required=False, max_digits=12, decimal_places=2,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej. 60000"}),
    )

    class Meta:
        model = SearchProfile
        fields = [
            "name",
            "operation_type",
            "province",
            "geography_scope",
            "municipalities",
            "geographic_area",
            "property_types",
            "min_price",
            "max_price",
            "min_area_m2",
            "min_bedrooms",
            "ai_prompt",
            "automation_enabled",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ej. Córdoba inversión"}),
            "min_price": forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej. 60000"}),
            "max_price": forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej. 60000"}),
            "min_area_m2": forms.NumberInput(attrs={"step": "0.01", "placeholder": "Ej. 60"}),
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
            self.fields["municipalities"].initial = ",".join(self.instance.municipalities or [])
        self.fields["geographic_area"].queryset = GeographicArea.objects.filter(is_active=True)

    def clean_municipalities(self):
        raw = self.cleaned_data.get("municipalities") or ""
        values, seen = [], set()
        for part in raw.split(","):
            value = " ".join(part.strip().split())
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                values.append(value)
        return values

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("geography_scope")
        municipalities = cleaned.get("municipalities") or []
        area = cleaned.get("geographic_area")
        required_count = 1 if scope == SearchProfile.GeographyScope.MUNICIPALITY else 2
        if scope in {SearchProfile.GeographyScope.MUNICIPALITY, SearchProfile.GeographyScope.MULTI_MUNICIPALITY} and len(municipalities) < required_count:
            self.add_error("municipalities", f"Añade al menos {required_count} municipio{'s' if required_count > 1 else ''}.")
        if scope == SearchProfile.GeographyScope.MUNICIPALITY and len(municipalities) > 1:
            self.add_error("municipalities", "Para un único municipio, deja solo uno.")
        if scope == SearchProfile.GeographyScope.NAMED_AREA and not area:
            self.add_error("geographic_area", "Selecciona una comarca o zona guardada.")
        if area and cleaned.get("province") and area.province != cleaned["province"]:
            self.add_error("geographic_area", "La zona guardada debe pertenecer a la provincia seleccionada.")
        if cleaned.get("min_price") is not None and cleaned.get("max_price") is not None and cleaned["min_price"] > cleaned["max_price"]:
            self.add_error("max_price", "El precio máximo debe ser igual o superior al mínimo.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.property_types = self.cleaned_data.get("property_types", [])
        scope = self.cleaned_data.get("geography_scope")
        obj.municipalities = (
            self.cleaned_data.get("municipalities", [])
            if scope in {SearchProfile.GeographyScope.MUNICIPALITY, SearchProfile.GeographyScope.MULTI_MUNICIPALITY}
            else []
        )
        if scope != SearchProfile.GeographyScope.NAMED_AREA:
            obj.geographic_area = None
        if commit:
            obj.save()
        return obj
