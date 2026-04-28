from django.db import models


class AIProviderConfig(models.Model):
    class ProviderCode(models.TextChoices):
        OPENAI = "openai", "OpenAI"
        QWEN = "qwen", "Qwen"
        DEEPSEEK = "deepseek", "DeepSeek"
        OTHER = "other", "Otro"

    name = models.CharField("nombre", max_length=120)
    provider_code = models.CharField(
        "proveedor",
        max_length=20,
        choices=ProviderCode.choices,
        default=ProviderCode.OPENAI,
    )
    model_name = models.CharField("modelo", max_length=120)
    base_url = models.URLField("base URL", blank=True)
    api_key = models.TextField("API key", blank=True)

    is_active = models.BooleanField("activo", default=True)
    is_default = models.BooleanField("proveedor por defecto", default=False)

    supports_web_search = models.BooleanField("soporta búsqueda web", default=True)
    supports_reasoning = models.BooleanField("soporta reasoning", default=True)

    priority_order = models.PositiveSmallIntegerField("prioridad de fallback", default=10)

    notes = models.TextField("notas", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Configuración de proveedor IA"
        verbose_name_plural = "Configuraciones de proveedores IA"
        ordering = ["priority_order", "name"]
        indexes = [
            models.Index(fields=["provider_code"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_default"]),
            models.Index(fields=["priority_order"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.model_name}"

    @property
    def masked_api_key(self) -> str:
        value = (self.api_key or "").strip()
        if len(value) <= 8:
            return "********" if value else ""
        return f"{value[:4]}...{value[-4:]}"

    def save(self, *args, **kwargs):
        if self.is_default:
            AIProviderConfig.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)