from django.db import models
from django.conf import settings

class SearchProfile(models.Model):
    class OperationType(models.TextChoices):
        SALE = "sale", "Venta"
        RENT = "rent", "Alquiler"

    class PropertyType(models.TextChoices):
        HOUSE = "house", "Casa"
        FLAT = "flat", "Piso"
        LAND = "land", "Terreno"
        COMMERCIAL = "commercial", "Local"

    class Status(models.TextChoices):
        ACTIVE = "active", "Activa"
        PAUSED = "paused", "Pausada"
        CLOSED_WITH_OPPORTUNITY = "closed_with_opportunity", "Cerrada con oportunidad"
        CLOSED_EMPTY = "closed_empty", "Cerrada desierta"
        ARCHIVED = "archived", "Archivada"

    class Color(models.TextChoices):
        BLUE = "blue", "Azul"
        GREEN = "green", "Verde"
        ORANGE = "orange", "Naranja"
        PURPLE = "purple", "Morado"
        RED = "red", "Rojo"
        TEAL = "teal", "Turquesa"

    name = models.CharField("nombre", max_length=150)
    operation_type = models.CharField(
        "tipo de operación",
        max_length=20,
        choices=OperationType.choices,
        default=OperationType.SALE,
    )
    province = models.CharField("provincia", max_length=100)
    zone = models.CharField("zona / municipio", max_length=150, blank=True)
    property_types = models.JSONField("tipos de propiedad", default=list, blank=True)
    max_price = models.DecimalField("precio máximo", max_digits=12, decimal_places=2, null=True, blank=True)
    min_bedrooms = models.PositiveSmallIntegerField("dormitorios mínimos", null=True, blank=True)
    ai_prompt = models.TextField("texto guía para IA", blank=True)

    status = models.CharField(
        "estado",
        max_length=40,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    color = models.CharField(
        "color operativo",
        max_length=20,
        choices=Color.choices,
        blank=True,
    )
    automation_enabled = models.BooleanField(
        "automatización IA activada",
        default=False,
        help_text="Si está desactivado, esta búsqueda no se ejecutará automáticamente.",
    )

    # Campo mantenido por compatibilidad con vistas/filtros existentes.
    is_active = models.BooleanField("activa", default=True)

    closed_at = models.DateTimeField("fecha de cierre", null=True, blank=True)
    selected_opportunity = models.ForeignKey(
        "seguimiento.PropertyOpportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_for_searches",
        verbose_name="oportunidad seleccionada",
    )
    outcome_notes = models.TextField("notas de desenlace", blank=True)

    notes = models.TextField("notas", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="search_profiles",
        verbose_name="propietario",
    )
    
    class Meta:
        verbose_name = "Perfil de búsqueda"
        verbose_name_plural = "Perfiles de búsqueda"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["province"]),
            models.Index(fields=["zone"]),
            models.Index(fields=["operation_type"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["status"]),
            models.Index(fields=["color"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self) -> str:
        return self.name

    def property_types_display(self) -> str:
        labels = dict(self.PropertyType.choices)
        values = self.property_types or []
        return ", ".join(labels.get(v, v) for v in values)


class SearchRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        RUNNING = "running", "En ejecución"
        COMPLETED = "completed", "Completada"
        COMPLETED_WITH_ERRORS = "completed_with_errors", "Completada con errores"
        FAILED = "failed", "Fallida"

    class ExecutionMode(models.TextChoices):
        MOCK = "mock", "Mock"
        AI_DISCOVERY = "ai_discovery", "Exploración IA"
        PORTAL = "portal", "Portal"
        EMAIL = "email", "Email"

    search_profile = models.ForeignKey(
        SearchProfile,
        on_delete=models.CASCADE,
        related_name="runs",
        verbose_name="perfil de búsqueda",
    )

    status = models.CharField(
        "estado",
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
    )
    execution_mode = models.CharField(
        "modo de ejecución",
        max_length=30,
        choices=ExecutionMode.choices,
        default=ExecutionMode.MOCK,
    )

    provider = models.CharField("proveedor", max_length=100, blank=True)
    model_name = models.CharField("modelo", max_length=100, blank=True)

    query_text = models.TextField("consulta generada", blank=True)
    filters_snapshot = models.JSONField("snapshot de filtros", default=dict, blank=True)
    raw_response = models.JSONField("respuesta cruda", default=dict, blank=True)
    warnings = models.JSONField("warnings", default=list, blank=True)
    error_message = models.TextField("error", blank=True)

    started_at = models.DateTimeField("inicio", null=True, blank=True)
    finished_at = models.DateTimeField("fin", null=True, blank=True)

    total_candidates = models.PositiveIntegerField("candidatos totales", default=0)
    total_valid_candidates = models.PositiveIntegerField("candidatos válidos", default=0)
    total_found = models.PositiveIntegerField("total encontrados", default=0)
    total_new = models.PositiveIntegerField("total nuevos", default=0)
    total_updated = models.PositiveIntegerField("total actualizados", default=0)
    total_errors = models.PositiveIntegerField("total errores", default=0)

    run_notes = models.TextField("notas de ejecución", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Ejecución de búsqueda"
        verbose_name_plural = "Ejecuciones de búsqueda"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["execution_mode"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.search_profile.name} - {self.created_at:%Y-%m-%d %H:%M}"