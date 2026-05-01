from django.db import models

from apps.ia.models import AIProviderConfig


class SystemSettings(models.Model):
    company_name = models.CharField("nombre de la empresa", max_length=150)
    company_email = models.EmailField("email principal", blank=True)
    company_phone = models.CharField("teléfono", max_length=50, blank=True)
    company_website = models.URLField("web", blank=True)
    company_notes = models.TextField("información relevante", blank=True)
    logo = models.ImageField("logo", upload_to="branding/", blank=True, null=True)
    public_logo = models.ImageField(
        "logo público / landing",
        upload_to="branding/",
        blank=True,
        null=True,
        help_text="Logo recomendado para la landing pública sobre fondo claro.",
    )

    login_logo = models.ImageField(
        "logo para login",
        upload_to="branding/",
        blank=True,
        null=True,
    )

    default_ai_provider = models.ForeignKey(
        AIProviderConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="system_settings_default",
        verbose_name="proveedor IA por defecto",
    )

    favicon = models.ImageField(
        "favicon",
        upload_to="branding/",
        blank=True,
        null=True,
        help_text="Icono pequeño del navegador. Recomendado: PNG/ICO cuadrado.",
    )

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Configuración del sistema"
        verbose_name_plural = "Configuración del sistema"

    def __str__(self) -> str:
        return self.company_name or "Configuración SOOI"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(
            pk=1,
            defaults={
                "company_name": "SOOI",
            },
        )
        return obj