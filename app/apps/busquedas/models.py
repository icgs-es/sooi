from django.db import models
from django.conf import settings


class GeographicArea(models.Model):
    class AreaType(models.TextChoices):
        COMARCA = "comarca", "Comarca"
        CUSTOM = "custom", "Zona personalizada"

    name = models.CharField("nombre", max_length=150)
    area_type = models.CharField("tipo", max_length=20, choices=AreaType.choices)
    province = models.CharField("provincia", max_length=100)
    municipalities = models.JSONField("municipios", default=list)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "Área geográfica"
        verbose_name_plural = "Áreas geográficas"
        ordering = ["province", "name"]
        constraints = [
            models.UniqueConstraint(fields=["province", "name"], name="unique_geographic_area_name_per_province"),
        ]

    def __str__(self):
        return f"{self.name} ({self.province})"

class SearchProfile(models.Model):
    class GeographyScope(models.TextChoices):
        MUNICIPALITY = "municipality", "Un municipio"
        MULTI_MUNICIPALITY = "multi_municipality", "Varios municipios"
        NAMED_AREA = "named_area", "Comarca o zona guardada"
        PROVINCE = "province", "Toda la provincia"
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
    geography_scope = models.CharField(
        "ámbito geográfico", max_length=30, choices=GeographyScope.choices, blank=True,
        help_text="Vacío indica un perfil antiguo que todavía se resuelve desde zona.",
    )
    municipalities = models.JSONField("municipios seleccionados", default=list, blank=True)
    geographic_area = models.ForeignKey(
        GeographicArea, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="search_profiles", verbose_name="comarca o zona guardada",
    )
    property_types = models.JSONField("tipos de propiedad", default=list, blank=True)
    min_price = models.DecimalField("precio mínimo", max_digits=12, decimal_places=2, null=True, blank=True)
    max_price = models.DecimalField("precio máximo", max_digits=12, decimal_places=2, null=True, blank=True)
    min_area_m2 = models.DecimalField("metros mínimos", max_digits=10, decimal_places=2, null=True, blank=True)
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

    def canonical_search_locations(self):
        """Canonical geography first; legacy zone is intentionally last."""
        if self.geography_scope == self.GeographyScope.PROVINCE:
            return []
        if self.geography_scope == self.GeographyScope.NAMED_AREA and self.geographic_area_id:
            return list(self.geographic_area.municipalities or [])
        if self.geography_scope in {self.GeographyScope.MUNICIPALITY, self.GeographyScope.MULTI_MUNICIPALITY}:
            return list(self.municipalities or [])
        return [part.strip() for part in (self.zone or "").split(",") if part.strip()]

    def resolved_search_geography(self):
        """Opt-in runtime view; raw model fields remain untouched."""
        from .geography_runtime import resolve_profile_geography
        return resolve_profile_geography(self)

    def geography_display(self):
        if self.geography_scope == self.GeographyScope.PROVINCE:
            return f"Toda la provincia de {self.province}"
        if self.geography_scope == self.GeographyScope.NAMED_AREA and self.geographic_area_id:
            return self.geographic_area.name
        locations = self.canonical_search_locations()
        return ", ".join(locations) if locations else (self.zone or self.province)


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

    class SearchMode(models.TextChoices):
        LEGACY = "legacy", "No gobernada (histórica)"
        FREE = "free", "Free"
        ECO = "eco", "Eco"
        AMPLIA = "amplia", "Amplia"
        PROFUNDA = "profunda", "Profunda"

    class CoverageStatus(models.TextChoices):
        NOT_EVALUATED = "not_evaluated", "No evaluada / histórica"
        FULL = "full", "Completa"
        PARTIAL = "partial", "Parcial"
        LIMITED_BY_PLAN = "limited_by_plan", "Limitada por plan"
        LIMITED_BY_BUDGET = "limited_by_budget", "Limitada por presupuesto"
        DEGRADED_PROVIDER = "degraded_provider", "Proveedor degradado"
        NO_RESULTS = "no_results", "Sin resultados"
        FAILED = "failed", "Fallida"

    class StopReason(models.TextChoices):
        NONE = "none", "Sin motivo / no establecido"
        SUFFICIENT_COVERAGE = "sufficient_coverage", "Cobertura suficiente"
        BUDGET_EXHAUSTED = "budget_exhausted", "Presupuesto agotado"
        PROVIDER_HARD_FAILURE = "provider_hard_failure", "Fallo duro del proveedor"
        PROVIDER_OUTAGE = "provider_outage", "Caída del proveedor"
        NO_APPLICABLE_SOURCES = "no_applicable_sources", "Sin fuentes aplicables"
        PLAN_LIMIT = "plan_limit", "Límite del plan"
        USER_CANCELLED = "user_cancelled", "Cancelada por el usuario"
        COMPLETED_PLAN = "completed_plan", "Plan completado"
        FAILED_INTERNAL = "failed_internal", "Fallo interno"

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

    # SR0.3A.2: additive governance snapshot. Legacy rows deliberately remain
    # distinguishable from governed searches.
    governance_enabled = models.BooleanField(default=False)
    governance_version = models.CharField(max_length=20, blank=True)
    search_mode = models.CharField(
        max_length=20, choices=SearchMode.choices, default=SearchMode.LEGACY,
    )
    coverage_status = models.CharField(
        max_length=30, choices=CoverageStatus.choices, default=CoverageStatus.NOT_EVALUATED,
    )
    stop_reason = models.CharField(
        max_length=30, choices=StopReason.choices, default=StopReason.NONE,
    )
    budget_max_credits = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_reserved_credits = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_consumed_credits = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    sources_planned = models.PositiveIntegerField(default=0)
    sources_executed = models.PositiveIntegerField(default=0)
    sources_omitted = models.PositiveIntegerField(default=0)
    calls_planned = models.PositiveIntegerField(default=0)
    calls_attempted = models.PositiveIntegerField(default=0)
    calls_succeeded = models.PositiveIntegerField(default=0)
    cache_hits = models.PositiveIntegerField(default=0)
    estimated_internal_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    actual_internal_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    search_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    reused_from_search_run = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reuse_runs",
    )

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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(governance_enabled=False)
                    | models.Q(budget_max_credits__isnull=True)
                    | (
                        models.Q(budget_reserved_credits__isnull=False)
                        & models.Q(budget_reserved_credits__lte=models.F("budget_max_credits"))
                    )
                ),
                name="searchrun_reserved_lte_max",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(governance_enabled=False)
                    | models.Q(budget_max_credits__isnull=True)
                    | (
                        models.Q(budget_reserved_credits__isnull=False)
                        & models.Q(budget_consumed_credits__isnull=False)
                        & models.Q(budget_consumed_credits__lte=models.F("budget_reserved_credits"))
                    )
                ),
                name="searchrun_consumed_lte_reserved",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.search_profile.name} - {self.created_at:%Y-%m-%d %H:%M}"
