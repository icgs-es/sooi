from django import forms

from .models import Source


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = [
            "name",
            "code",
            "base_url",
            "source_type",
            "is_active",
            "is_verified",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ej. Fotocasa"}),
            "code": forms.TextInput(attrs={"placeholder": "Ej. fotocasa"}),
            "base_url": forms.URLInput(attrs={"placeholder": "https://www.fotocasa.es"}),
            "source_type": forms.Select(),
            "is_active": forms.CheckboxInput(),
            "is_verified": forms.CheckboxInput(),
            "notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Notas internas sobre la fuente"}),
        }

    def clean_code(self):
        value = self.cleaned_data["code"].strip().lower()
        return value