from django.conf import settings
from django.db import models

from apps.inmuebles.models import CapturedProperty


class BrokerCompany(models.Model):
    name = models.CharField("nombre", max_length=150)
    website = models.URLField("web", blank=True)
    phone = models.CharField("teléfono", max_length=50, blank=True)
    email = models.EmailField("email", blank=True)
    notes = models.TextField("notas", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_broker_companies",
        verbose_name="propietario",
    )
    
    class Meta:
        verbose_name = "Comercializadora"
        verbose_name_plural = "Comercializadoras"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OpportunityContact(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Propietario"
        AGENT = "agent", "Agente"
        BROKER = "broker", "Intermediario"
        ASSISTANT = "assistant", "Asistente"
        OTHER = "other", "Otro"

    class PreferredChannel(models.TextChoices):
        PHONE = "phone", "Teléfono"
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"
        UNKNOWN = "unknown", "Desconocido"

    full_name = models.CharField("nombre completo", max_length=150)
    role = models.CharField("rol", max_length=20, choices=Role.choices, default=Role.OTHER)
    phone = models.CharField("teléfono", max_length=50, blank=True)
    email = models.EmailField("email", blank=True)
    preferred_channel = models.CharField(
        "canal preferido",
        max_length=20,
        choices=PreferredChannel.choices,
        default=PreferredChannel.UNKNOWN,
    )
    notes = models.TextField("notas", blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_opportunity_contacts",
        verbose_name="propietario",
    )
    
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Contacto de oportunidad"
        verbose_name_plural = "Contactos de oportunidad"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name


class PropertyOpportunity(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Nueva"
        ACTIVE = "active", "Activa"
        ANALYSIS = "analysis", "En análisis"
        NEGOTIATION = "negotiation", "En negociación"
        DISCARDED = "discarded", "Descartada"
        CLOSED = "closed", "Cerrada"

    class Priority(models.TextChoices):
        LOW = "low", "Baja"
        MEDIUM = "medium", "Media"
        HIGH = "high", "Alta"

    class NextActionType(models.TextChoices):
        CALL = "call", "Llamar"
        WAIT_RESPONSE = "wait_response", "Esperar respuesta"
        REQUEST_DOCS = "request_docs", "Pedir documentación"
        SCHEDULE_VISIT = "schedule_visit", "Agendar visita"
        VALIDATE_DATA = "validate_data", "Validar datos"
        REVIEW_PROFITABILITY = "review_profitability", "Revisar rentabilidad"
        MAKE_OFFER = "make_offer", "Hacer oferta"
        NEGOTIATE = "negotiate", "Negociar"
        DISCARD = "discard", "Descartar"
        OTHER = "other", "Otra"

    class SimpleNoteStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        REQUESTED = "requested", "Solicitada"
        RECEIVED = "received", "Recibida"
        REVIEWED = "reviewed", "Revisada"
        NOT_APPLICABLE = "not_applicable", "No aplica"

    captured_property = models.OneToOneField(
        CapturedProperty,
        on_delete=models.CASCADE,
        related_name="opportunity",
        verbose_name="inmueble captado",
    )

    search_profile = models.ForeignKey(
        "busquedas.SearchProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
        verbose_name="búsqueda",
    )

    title = models.CharField("título", max_length=255)

    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    priority = models.CharField(
        "prioridad",
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="property_opportunities",
        verbose_name="asignado a",
    )

    broker_company = models.ForeignKey(
        BrokerCompany,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
        verbose_name="comercializadora",
    )
    main_contact = models.ForeignKey(
        OpportunityContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
        verbose_name="contacto principal",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_property_opportunities",
        verbose_name="propietario",
    )
    
    next_action_type = models.CharField(
        "tipo de siguiente acción",
        max_length=30,
        choices=NextActionType.choices,
        default=NextActionType.OTHER,
    )
    next_action_notes = models.CharField("detalle de siguiente acción", max_length=255, blank=True)
    next_review_at = models.DateTimeField("próxima revisión", null=True, blank=True)

    opportunity_score = models.PositiveSmallIntegerField(
        "score oportunidad",
        null=True,
        blank=True,
    )

    province = models.CharField("provincia", max_length=100, blank=True)
    municipality = models.CharField("municipio", max_length=100, blank=True)
    zone = models.CharField("zona", max_length=150, blank=True)
    postal_code = models.CharField("código postal", max_length=20, blank=True)
    address_text = models.CharField("dirección o referencia", max_length=255, blank=True)

    asking_price_current = models.DecimalField(
        "precio actual",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    target_price_internal = models.DecimalField(
        "precio objetivo interno",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    max_offer_price = models.DecimalField(
        "oferta máxima",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expected_rent_monthly = models.DecimalField(
        "renta mensual esperada",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    estimated_gross_yield = models.DecimalField(
        "rentabilidad bruta estimada (%)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    cadastral_reference = models.CharField("referencia catastral", max_length=50, blank=True)
    simple_note_status = models.CharField(
        "estado nota simple",
        max_length=20,
        choices=SimpleNoteStatus.choices,
        default=SimpleNoteStatus.PENDING,
    )
    simple_note_date = models.DateField("fecha nota simple", null=True, blank=True)

    summary = models.TextField("resumen operativo", blank=True)
    decision_notes = models.TextField("notas de decisión", blank=True)
    discard_reason = models.TextField("motivo de descarte", blank=True)

    last_activity_at = models.DateTimeField("última actividad", null=True, blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Oportunidad inmobiliaria"
        verbose_name_plural = "Oportunidades inmobiliarias"
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["next_action_type"]),
            models.Index(fields=["next_review_at"]),
            models.Index(fields=["province"]),
            models.Index(fields=["municipality"]),
            models.Index(fields=["last_activity_at"]),
            models.Index(fields=["search_profile"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def source_name(self) -> str:
        if self.captured_property and self.captured_property.source:
            return self.captured_property.source.name
        return ""

    @property
    def source_url(self) -> str:
        if self.captured_property:
            return self.captured_property.source_url or ""
        return ""

    @property
    def score_band(self) -> str:
        if self.opportunity_score is None:
            return ""
        if self.opportunity_score < 40:
            return "Baja"
        if self.opportunity_score < 70:
            return "Media"
        return "Alta"

    def save(self, *args, **kwargs):
        if self.asking_price_current and self.expected_rent_monthly:
            try:
                self.estimated_gross_yield = (
                    (self.expected_rent_monthly * 12) / self.asking_price_current
                ) * 100
            except Exception:
                pass

        super().save(*args, **kwargs)

        review_task = (
            FollowUpTask.objects.filter(
                property_opportunity=self,
                task_type=FollowUpTask.TaskType.REVIEW,
                status__in=[
                    FollowUpTask.Status.OPEN,
                    FollowUpTask.Status.IN_PROGRESS,
                ],
            )
            .order_by("-created_at")
            .first()
        )

        if self.next_review_at:
            title = f"Revisión de oportunidad: {self.title}"
            description_parts = [
                "Tarea automática generada desde la oportunidad.",
            ]

            if self.next_action_type:
                description_parts.append(
                    f"Siguiente acción: {self.get_next_action_type_display()}."
                )

            if self.next_action_notes:
                description_parts.append(
                    f"Detalle: {self.next_action_notes}"
                )

            description = "\n".join(description_parts)

            defaults = {
                "title": title,
                "description": description,
                "due_date": self.next_review_at,
                "assigned_to": self.assigned_to,
                "captured_property": self.captured_property,
                "priority": self.priority,
                "owner": self.owner,
            }

            if review_task:
                for field, value in defaults.items():
                    setattr(review_task, field, value)
                review_task.save()
            else:
                FollowUpTask.objects.create(
                    property_opportunity=self,
                    task_type=FollowUpTask.TaskType.REVIEW,
                    status=FollowUpTask.Status.OPEN,
                    **defaults,
                )
        else:
            if review_task:
                review_task.status = FollowUpTask.Status.CANCELLED
                review_task.save(update_fields=["status", "updated_at"])

class OpportunityActivity(models.Model):
    class ActivityType(models.TextChoices):
        CREATED = "created", "Creación"
        NOTE = "note", "Nota"
        CALL = "call", "Llamada"
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"
        VISIT = "visit", "Visita"
        DOCUMENT_REQUEST = "document_request", "Solicitud de documento"
        DOCUMENT_RECEIVED = "document_received", "Documento recibido"
        STATUS_CHANGE = "status_change", "Cambio de estado"
        PRICE_UPDATE = "price_update", "Actualización de precio"
        TASK = "task", "Tarea"

    opportunity = models.ForeignKey(
        PropertyOpportunity,
        on_delete=models.CASCADE,
        related_name="activities",
        verbose_name="oportunidad",
    )

    activity_type = models.CharField(
        "tipo de actividad",
        max_length=30,
        choices=ActivityType.choices,
        default=ActivityType.NOTE,
    )
    summary = models.CharField("resumen", max_length=220)
    details = models.TextField("detalle", blank=True)
    extra_data = models.JSONField("datos extra", default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="opportunity_activities",
        verbose_name="creado por",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        verbose_name = "Actividad de oportunidad"
        verbose_name_plural = "Actividades de oportunidad"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["activity_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_activity_type_display()} · {self.summary}"
    
class Alert(models.Model):
    class AlertType(models.TextChoices):
        NEW_CAPTURE = "new_capture", "Nueva captación"
        PRICE_DROP = "price_drop", "Bajada de precio"
        REVIEW_OVERDUE = "review_overdue", "Revisión vencida"
        POSSIBLE_DUPLICATE = "possible_duplicate", "Posible duplicado"
        LISTING_UNAVAILABLE = "listing_unavailable", "Anuncio no disponible"
        OPPORTUNITY_INACTIVE = "opportunity_inactive", "Oportunidad inactiva"
        INFO = "info", "Información"

    class Severity(models.TextChoices):
        LOW = "low", "Baja"
        MEDIUM = "medium", "Media"
        HIGH = "high", "Alta"

    class Status(models.TextChoices):
        NEW = "new", "Nueva"
        SEEN = "seen", "Vista"
        RESOLVED = "resolved", "Resuelta"
        DISMISSED = "dismissed", "Descartada"

    alert_type = models.CharField("tipo", max_length=40, choices=AlertType.choices, default=AlertType.INFO)
    severity = models.CharField("severidad", max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField("estado", max_length=15, choices=Status.choices, default=Status.NEW)
    description = models.TextField("descripción", blank=True)
    title = models.CharField("título", max_length=200)
    message = models.TextField("mensaje", blank=True)

    captured_property = models.ForeignKey(
        CapturedProperty,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alerts",
        verbose_name="inmueble captado",
    )
    property_opportunity = models.ForeignKey(
        PropertyOpportunity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alerts",
        verbose_name="oportunidad",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_alerts",
        verbose_name="propietario",
    )
    
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Alerta"
        verbose_name_plural = "Alertas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["alert_type"]),
            models.Index(fields=["severity"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class FollowUpTask(models.Model):
    class TaskType(models.TextChoices):
        REVIEW = "review", "Revisión"
        PRICE_CHECK = "price_check", "Revisión de precio"
        CONTACT_OWNER = "contact_owner", "Contactar"
        VALIDATE_DATA = "validate_data", "Validar datos"
        FOLLOW_UP = "follow_up", "Seguimiento"

    class Status(models.TextChoices):
        OPEN = "open", "Abierta"
        IN_PROGRESS = "in_progress", "En progreso"
        DONE = "done", "Hecha"
        CANCELLED = "cancelled", "Cancelada"

    class Priority(models.TextChoices):
        LOW = "low", "Baja"
        MEDIUM = "medium", "Media"
        HIGH = "high", "Alta"

    task_type = models.CharField("tipo", max_length=30, choices=TaskType.choices, default=TaskType.FOLLOW_UP)
    title = models.CharField("título", max_length=200)
    description = models.TextField("descripción", blank=True)

    status = models.CharField("estado", max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField("prioridad", max_length=10, choices=Priority.choices, default=Priority.MEDIUM)

    due_date = models.DateTimeField("fecha límite", null=True, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_followup_tasks",
        verbose_name="propietario",
    )
    
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="followup_tasks",
        verbose_name="asignado a",
    )

    captured_property = models.ForeignKey(
        CapturedProperty,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="inmueble captado",
    )
    property_opportunity = models.ForeignKey(
        PropertyOpportunity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="oportunidad",
    )

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Tarea de seguimiento"
        verbose_name_plural = "Tareas de seguimiento"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["due_date"]),
        ]

    def __str__(self) -> str:
        return self.title