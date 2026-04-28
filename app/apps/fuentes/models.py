from django.db import models


class Source(models.Model):
    class SourceType(models.TextChoices):
        PORTAL = "portal", "Portal"
        WEB = "web", "Web"
        MANUAL = "manual", "Manual"
        API = "api", "API"

    name = models.CharField("nombre", max_length=120)
    code = models.SlugField("código", max_length=50, unique=True)
    base_url = models.URLField("url base", blank=True)
    source_type = models.CharField(
        "tipo de fuente",
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.PORTAL,
    )
    is_active = models.BooleanField("activa", default=True)
    is_verified = models.BooleanField("verificada", default=True)
    notes = models.TextField("notas", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Fuente"
        verbose_name_plural = "Fuentes"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_verified"]),
            models.Index(fields=["source_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"