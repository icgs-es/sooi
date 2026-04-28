from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from .models import SystemSettings


class SystemSettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSettings
        fields = [
            "company_name",
            "company_email",
            "company_phone",
            "company_website",
            "company_notes",
            "logo",
            "login_logo",
            "default_ai_provider",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"placeholder": "Nombre de la empresa"}),
            "company_email": forms.EmailInput(attrs={"placeholder": "Email principal"}),
            "company_phone": forms.TextInput(attrs={"placeholder": "Teléfono"}),
            "company_website": forms.URLInput(attrs={"placeholder": "https://..."}),
            "login_logo": forms.ClearableFileInput(),
            "company_notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Información interna relevante de la empresa o del sistema.",
                }
            ),
        }
        
User = get_user_model()


class InternalUserCreateForm(UserCreationForm):
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["username"].label = "Usuario"
        self.fields["first_name"].label = "Nombre"
        self.fields["last_name"].label = "Apellidos"
        self.fields["password1"].label = "Contraseña"
        self.fields["password2"].label = "Confirmar contraseña"

        self.fields["username"].widget.attrs.update({"placeholder": "Ej. comercial1"})
        self.fields["first_name"].widget.attrs.update({"placeholder": "Nombre"})
        self.fields["last_name"].widget.attrs.update({"placeholder": "Apellidos"})
        self.fields["email"].widget.attrs.update({"placeholder": "email@empresa.com"})

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        if commit:
            user.save()
        return user
    
class InternalUserUpdateForm(forms.ModelForm):
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["username"].label = "Usuario"
        self.fields["first_name"].label = "Nombre"
        self.fields["last_name"].label = "Apellidos"
        self.fields["is_active"].label = "Activo"

        self.fields["username"].widget.attrs.update({"placeholder": "Ej. comercial1"})
        self.fields["first_name"].widget.attrs.update({"placeholder": "Nombre"})
        self.fields["last_name"].widget.attrs.update({"placeholder": "Apellidos"})
        self.fields["email"].widget.attrs.update({"placeholder": "email@empresa.com"})