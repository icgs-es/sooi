from django import forms

from .models import AIProviderConfig


class AIProviderConfigForm(forms.ModelForm):
    class Meta:
        model = AIProviderConfig
        fields = [
            "name",
            "provider_code",
            "model_name",
            "base_url",
            "api_key",
            "is_active",
            "is_default",
            "supports_web_search",
            "supports_reasoning",
            "priority_order",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ej. OpenAI principal"}),
            "model_name": forms.TextInput(attrs={"placeholder": "Ej. gpt-5.4"}),
            "base_url": forms.URLInput(attrs={"placeholder": "https://api.openai.com/v1"}),
            "api_key": forms.PasswordInput(
                attrs={"placeholder": "API key del proveedor"},
                render_value=True,
            ),
            "priority_order": forms.NumberInput(attrs={"min": 1}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }