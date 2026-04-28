from django.db import models
from django.conf import settings
from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source

class CapturedProperty(models.Model):
    class EntryMode(models.TextChoices):
        STRUCTURED_CAPTURE = "structured_capture", "Captación estructurada"
        AI_EXPLORATION = "ai_exploration", "Exploración IA"
        MANUAL = "manual", "Manual"

    class OperationType(models.TextChoices):
        SALE = "sale", "Venta"
        RENT = "rent", "Alquiler"

    class PropertyType(models.TextChoices):
        HOUSE = "house", "Casa"
        LAND = "land", "Terreno"
        FLAT = "flat", "Piso"
        COMMERCIAL = "commercial", "Local"

    class Status(models.TextChoices):
        CAPTURED = "captured", "Captado"
        IN_REVIEW = "in_review", "En revisión"
        VALIDATED = "validated", "Validado"
        DISCARDED = "discarded", "Descartado"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        REVIEWED = "reviewed", "Revisado"
        REVIEWED_WITH_CHANGES = "reviewed_with_changes", "Revisado con cambios"

    search_profile = models.ForeignKey(
        SearchProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captured_properties",
        verbose_name="perfil de búsqueda",
    )
    search_run = models.ForeignKey(
        SearchRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captured_properties",
        verbose_name="ejecución de búsqueda",
    )
    source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="captured_properties",
        verbose_name="fuente",
    )

    entry_mode = models.CharField(
        "modo de entrada",
        max_length=30,
        choices=EntryMode.choices,
        default=EntryMode.STRUCTURED_CAPTURE,
    )
    operation_type = models.CharField(
        "tipo de operación",
        max_length=20,
        choices=OperationType.choices,
        default=OperationType.SALE,
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="captured_properties",
        verbose_name="propietario",
    )
    
    source_url = models.URLField("url origen", max_length=1000, blank=True)
    source_external_id = models.CharField("id externo", max_length=255, blank=True)

    title = models.CharField("título", max_length=255)
    description_raw = models.TextField("descripción original", blank=True)

    province = models.CharField("provincia", max_length=100, blank=True)
    municipality = models.CharField("municipio", max_length=120, blank=True)
    zone_text = models.CharField("zona", max_length=255, blank=True)

    property_type = models.CharField(
        "tipo de propiedad",
        max_length=20,
        choices=PropertyType.choices,
    )

    price = models.DecimalField("precio", max_digits=12, decimal_places=2, null=True, blank=True)
    bedrooms = models.PositiveSmallIntegerField("dormitorios", null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField("baños", null=True, blank=True)
    area_m2 = models.DecimalField("superficie m2", max_digits=10, decimal_places=2, null=True, blank=True)

    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.CAPTURED,
    )
    review_status = models.CharField(
        "estado de revisión",
        max_length=30,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )

    possible_duplicate = models.BooleanField(
        "posible duplicado",
        default=False,
    )
        
    is_interesting = models.BooleanField("interesante", default=False)

    ai_summary = models.TextField("resumen IA", blank=True)
    ai_signals = models.JSONField("señales IA", default=list, blank=True)
    ai_score = models.DecimalField("score IA", max_digits=5, decimal_places=2, null=True, blank=True)

    manual_notes = models.TextField("notas manuales", blank=True)
    discard_reason = models.CharField("motivo de descarte", max_length=100, blank=True)

    published_at_source = models.DateTimeField("publicado en origen", null=True, blank=True)
    captured_at = models.DateTimeField("captado en", auto_now_add=True)
    last_seen_at = models.DateTimeField("última vez visto", null=True, blank=True)
    last_reviewed_at = models.DateTimeField("última revisión", null=True, blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Inmueble captado"
        verbose_name_plural = "Inmuebles captados"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["review_status"]),
            models.Index(fields=["is_interesting"]),
            models.Index(fields=["operation_type"]),
            models.Index(fields=["property_type"]),
            models.Index(fields=["province"]),
            models.Index(fields=["municipality"]),
            models.Index(fields=["price"]),
            models.Index(fields=["captured_at"]),
            models.Index(fields=["source_external_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} [{self.source.code}]"
    