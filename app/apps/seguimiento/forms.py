from django import forms
from django.contrib.auth import get_user_model

from .models import Alert, BrokerCompany, OpportunityActivity, OpportunityContact, PropertyOpportunity

User = get_user_model()


class OpportunityForm(forms.ModelForm):
    next_review_at = forms.DateTimeField(
        label="Próxima revisión",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["assigned_to"].queryset = (
                User.objects.filter(pk=user.pk, is_active=True).order_by("username")
            )
            self.fields["broker_company"].queryset = (
                BrokerCompany.objects.filter(owner=user).order_by("name")
            )
            self.fields["main_contact"].queryset = (
                OpportunityContact.objects.filter(owner=user).order_by("full_name")
            )

    class Meta:
        model = PropertyOpportunity
        fields = [
            "title",
            "status",
            "priority",
            "assigned_to",
            "next_action_type",
            "next_action_notes",
            "next_review_at",
            "opportunity_score",
            "province",
            "municipality",
            "zone",
            "postal_code",
            "address_text",
            "asking_price_current",
            "target_price_internal",
            "max_offer_price",
            "expected_rent_monthly",
            "estimated_gross_yield",
            "broker_company",
            "main_contact",
            "cadastral_reference",
            "simple_note_status",
            "simple_note_date",
            "summary",
            "decision_notes",
            "discard_reason",
        ]
        widgets = {
            "title": forms.TextInput(),
            "status": forms.Select(),
            "priority": forms.Select(),
            "assigned_to": forms.Select(),
            "next_action_type": forms.Select(),
            "next_action_notes": forms.TextInput(attrs={"placeholder": "Ej. Recibir respuesta y agendar visita"}),
            "opportunity_score": forms.NumberInput(attrs={"min": 0, "max": 100}),
            "province": forms.TextInput(attrs={"placeholder": "Provincia"}),
            "municipality": forms.TextInput(attrs={"placeholder": "Municipio"}),
            "zone": forms.TextInput(attrs={"placeholder": "Zona / barrio / área"}),
            "postal_code": forms.TextInput(attrs={"placeholder": "Código postal"}),
            "address_text": forms.TextInput(attrs={"placeholder": "Dirección o referencia interna"}),
            "asking_price_current": forms.NumberInput(attrs={"step": "0.01"}),
            "target_price_internal": forms.NumberInput(attrs={"step": "0.01"}),
            "max_offer_price": forms.NumberInput(attrs={"step": "0.01"}),
            "expected_rent_monthly": forms.NumberInput(attrs={"step": "0.01"}),
            "estimated_gross_yield": forms.NumberInput(attrs={"step": "0.01"}),
            "broker_company": forms.Select(),
            "main_contact": forms.Select(),
            "cadastral_reference": forms.TextInput(),
            "simple_note_status": forms.Select(),
            "simple_note_date": forms.DateInput(attrs={"type": "date"}),
            "summary": forms.Textarea(attrs={"rows": 3}),
            "decision_notes": forms.Textarea(attrs={"rows": 4}),
            "discard_reason": forms.Textarea(attrs={"rows": 3}),
        }


class OpportunityActivityQuickForm(forms.ModelForm):
    class Meta:
        model = OpportunityActivity
        fields = [
            "activity_type",
            "summary",
            "details",
        ]
        widgets = {
            "summary": forms.TextInput(attrs={"placeholder": "Ej. Llamada realizada al contacto"}),
            "details": forms.Textarea(attrs={"rows": 3, "placeholder": "Detalle breve de lo ocurrido"}),
        }


class BrokerCompanyForm(forms.ModelForm):
    class Meta:
        model = BrokerCompany
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "Ej. Inmobiliaria Sierra Norte"}
            ),
        }


class OpportunityContactForm(forms.ModelForm):
    class Meta:
        model = OpportunityContact
        fields = ["full_name", "phone", "email", "role", "preferred_channel"]
        widgets = {
            "full_name": forms.TextInput(
                attrs={"placeholder": "Ej. Juan Pérez"}
            ),
            "phone": forms.TextInput(
                attrs={"placeholder": "Ej. 600123123"}
            ),
            "email": forms.EmailInput(
                attrs={"placeholder": "Ej. juan@email.com"}
            ),
            "role": forms.Select(),
            "preferred_channel": forms.Select(),
        }


class AlertForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["property_opportunity"].queryset = (
                PropertyOpportunity.objects.filter(owner=user).order_by("-created_at")
            )

    class Meta:
        model = Alert
        fields = ["title", "alert_type", "severity", "status", "property_opportunity", "description"]
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Ej. Revisar oportunidad sin respuesta"}
            ),
            "alert_type": forms.Select(),
            "severity": forms.Select(),
            "status": forms.Select(),
            "property_opportunity": forms.Select(),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Descripción breve de la alerta"}
            ),
        }