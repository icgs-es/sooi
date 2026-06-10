from .models import DemoRequest
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
            "public_logo",
            "login_logo",
            "favicon",
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

class DemoRequestForm(forms.ModelForm):
    class Meta:
        model = DemoRequest
        fields = ["name", "email", "phone", "profile_type", "message"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Nombre y apellidos"}),
            "email": forms.EmailInput(attrs={"placeholder": "tu@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "Teléfono opcional"}),
            "message": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": "Cuéntanos brevemente qué tipo de oportunidades inmobiliarias quieres gestionar con SOOI.",
            }),
        }


class RegistrationForm(forms.Form):
    first_name = forms.CharField(
        label="Nombre",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Nombre"}),
    )
    last_name = forms.CharField(
        label="Apellidos",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Apellidos"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"placeholder": "tu@email.com"}),
    )
    company = forms.CharField(
        label="Empresa (opcional)",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Tu empresa o proyecto"}),
    )
    password1 = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Mínimo 8 caracteres"}),
    )
    password2 = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Repite la contraseña"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este email.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cleaned
