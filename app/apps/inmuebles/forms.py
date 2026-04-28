from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.busquedas.forms import SPAIN_PROVINCE_CHOICES
from apps.busquedas.models import SearchProfile
from apps.fuentes.models import Source

from .models import CapturedProperty


ACTIVE_SEARCH_STATUSES = [
    SearchProfile.Status.ACTIVE,
    SearchProfile.Status.PAUSED,
]


class CapturedPropertyManualForm(forms.ModelForm):
    province = forms.ChoiceField(
        label="Provincia",
        choices=SPAIN_PROVINCE_CHOICES,
        required=False,
    )

    class Meta:
        model = CapturedProperty
        fields = [
            "search_profile",
            "source",
            "operation_type",
            "property_type",
            "title",
            "source_url",
            "price",
            "province",
            "municipality",
            "zone_text",
            "bedrooms",
            "bathrooms",
            "area_m2",
            "description_raw",
            "manual_notes",
        ]
        widgets = {
            "search_profile": forms.Select(),
            "source": forms.Select(),
            "operation_type": forms.Select(),
            "property_type": forms.Select(),
            "title": forms.TextInput(attrs={"placeholder": "Ej. Piso en Calle Doctor Rodríguez Blanco, 27"}),
            "source_url": forms.URLInput(attrs={"placeholder": "https://..."}),
            "price": forms.NumberInput(attrs={"step": "0.01", "placeholder": "Ej. 85000"}),
            "municipality": forms.TextInput(attrs={"placeholder": "Municipio"}),
            "zone_text": forms.TextInput(attrs={"placeholder": "Zona / calle / referencia"}),
            "bedrooms": forms.NumberInput(attrs={"min": 0}),
            "bathrooms": forms.NumberInput(attrs={"min": 0}),
            "area_m2": forms.NumberInput(attrs={"step": "0.01"}),
            "description_raw": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Descripción mínima operativa. Evitar copiar el anuncio completo.",
                }
            ),
            "manual_notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Notas internas. Contactar preferentemente desde la fuente original.",
                }
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        self.fields["source"].queryset = Source.objects.exclude(
            code__in=["exploracion-ia", "manual"]
        ).order_by("name")
        self.fields["source"].empty_label = "Selecciona fuente real"

        self.fields["search_profile"].label = "Búsqueda asociada"
        self.fields["search_profile"].required = True
        self.fields["search_profile"].empty_label = "Selecciona búsqueda asociada"

        qs = SearchProfile.objects.none()
        if user is not None:
            qs = SearchProfile.objects.filter(owner=user).filter(
                Q(status__in=ACTIVE_SEARCH_STATUSES)
                | Q(pk=getattr(self.instance, "search_profile_id", None))
            ).distinct().order_by("status", "name")

        self.fields["search_profile"].queryset = qs

    def clean_search_profile(self):
        search_profile = self.cleaned_data.get("search_profile")

        if not search_profile:
            raise ValidationError("Debes asociar la captación a una búsqueda activa.")

        if self.user is not None and search_profile.owner_id != self.user.id:
            raise ValidationError("La búsqueda seleccionada no pertenece a tu usuario.")

        return search_profile

    def save(self, commit=True):
        obj = super().save(commit=False)

        if obj._state.adding:
            obj.entry_mode = CapturedProperty.EntryMode.MANUAL
            obj.status = CapturedProperty.Status.CAPTURED
            obj.review_status = CapturedProperty.ReviewStatus.PENDING

        if commit:
            obj.save()

        return obj
