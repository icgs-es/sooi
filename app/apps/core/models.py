from datetime import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.ia.models import AIProviderConfig
from apps.core.plans import PLAN_CHOICES

User = get_user_model()


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

class DemoRequest(models.Model):
    class ProfileType(models.TextChoices):
        INVESTOR = "investor", "Inversor particular"
        CAPTOR = "captor", "Captador inmobiliario"
        AGENCY = "agency", "Inmobiliaria / agencia"
        OPERATOR = "operator", "Pequeño operador"
        OTHER = "other", "Otro"

    class Status(models.TextChoices):
        NEW = "new", "Nueva"
        CONTACTED = "contacted", "Contactado"
        CLOSED = "closed", "Cerrada"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        SENT = "sent", "Enviada"
        FAILED = "failed", "Fallida"

    name = models.CharField("nombre", max_length=160)
    email = models.EmailField("email")
    phone = models.CharField("teléfono", max_length=60, blank=True)
    profile_type = models.CharField(
        "perfil",
        max_length=20,
        choices=ProfileType.choices,
        default=ProfileType.INVESTOR,
    )
    message = models.TextField("mensaje", blank=True)
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    delivery_status = models.CharField(
        "estado de entrega", max_length=20,
        choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING,
    )
    notification_attempts = models.PositiveIntegerField("intentos de notificación", default=0)
    last_notification_error = models.CharField("último error de notificación", max_length=500, blank=True, default="")
    notified_at = models.DateTimeField("notificada", null=True, blank=True)
    source_domain = models.CharField("dominio origen", max_length=120, default="sooi.io", blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Solicitud de demo"
        verbose_name_plural = "Solicitudes de demo"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.email}"


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    company = models.CharField("empresa", max_length=200, blank=True)
    phone = models.CharField("teléfono", max_length=50, blank=True)
    trial_start = models.DateTimeField("inicio de trial", null=True, blank=True)
    trial_end = models.DateTimeField("fin de trial", null=True, blank=True)
    is_trial = models.BooleanField("en periodo de trial", default=True)
    plan = models.CharField("plan", max_length=20, choices=PLAN_CHOICES, default="professional")
    signup_source = models.CharField(
        "origen de registro",
        max_length=50,
        default="self_service",
    )

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def __str__(self):
        return self.user.email

    @property
    def trial_expired(self):
        return bool(self.trial_end and self.trial_end < timezone.now())


class NotificationPreference(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
        verbose_name="propietario",
    )
    daily_digest_enabled = models.BooleanField("resumen diario", default=False)
    daily_digest_time = models.TimeField("hora del resumen", default=time(8, 0))
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Preferencia de notificación"
        verbose_name_plural = "Preferencias de notificación"

    def __str__(self):
        return f"Preferencias de {self.owner}"


class DailyDigestDelivery(models.Model):
    class DeliveryType(models.TextChoices):
        DAILY_DIGEST = "daily_digest", "Resumen diario"

    class Channel(models.TextChoices):
        EMAIL = "email", "Correo electrónico"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Programado"
        PROCESSING = "processing", "Procesando"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Fallido"
        SKIPPED = "skipped", "Omitido"
        UNKNOWN = "unknown", "Desconocido"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_digest_deliveries",
        verbose_name="propietario",
    )
    delivery_type = models.CharField(
        "tipo de entrega",
        max_length=30,
        choices=DeliveryType.choices,
        default=DeliveryType.DAILY_DIGEST,
    )
    channel = models.CharField(
        "canal",
        max_length=20,
        choices=Channel.choices,
        default=Channel.EMAIL,
    )
    local_date = models.DateField("fecha local")
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        db_index=True,
    )
    scheduled_for = models.DateTimeField("programado para", null=True, blank=True)
    attempted_at = models.DateTimeField("intentado en", null=True, blank=True)
    sent_at = models.DateTimeField("enviado en", null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField("número de intentos", default=0)
    next_retry_at = models.DateTimeField("próximo reintento", null=True, blank=True)
    reason_code = models.CharField("código de motivo", max_length=80, blank=True)
    last_error_class = models.CharField("clase del último error", max_length=120, blank=True)
    payload_fingerprint = models.CharField("huella del contenido", max_length=64, blank=True)
    claim_token = models.UUIDField("token de asignación", null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField("fin de asignación", null=True, blank=True)
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Entrega de resumen diario"
        verbose_name_plural = "Entregas de resumen diario"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "delivery_type", "channel", "local_date"],
                name="core_daily_digest_delivery_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "local_date"], name="core_digest_status_date"),
            models.Index(fields=["owner", "local_date"], name="core_digest_owner_date"),
            models.Index(fields=["status", "next_retry_at"], name="core_digest_retry"),
        ]

    def __str__(self):
        return f"{self.owner_id} · {self.delivery_type} · {self.local_date}"


class SearchCreditAccountPeriod(models.Model):
    """Canonical commercial allowance snapshot for an owner and billing period."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="search_credit_periods")
    period_start = models.DateField()
    period_end = models.DateField()
    allowance_credits = models.DecimalField(max_digits=12, decimal_places=2)
    plan_code = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "period_start"], name="search_credit_owner_period_unique"),
            models.CheckConstraint(condition=models.Q(period_end__gt=models.F("period_start")), name="search_credit_period_dates_valid"),
            models.CheckConstraint(condition=models.Q(allowance_credits__gte=0), name="search_credit_allowance_nonnegative"),
        ]

    def __str__(self):
        return f"{self.owner_id}: {self.period_start}–{self.period_end}"


class SearchCreditLedgerEntry(models.Model):
    """Append-only, provider-independent SOOI Search Credit event."""

    class EntryType(models.TextChoices):
        RESERVATION = "reservation", "Reserva"
        SETTLEMENT = "settlement", "Consumo"
        RELEASE = "release", "Liberación"
        ADJUSTMENT = "adjustment", "Ajuste"

    account_period = models.ForeignKey(SearchCreditAccountPeriod, on_delete=models.PROTECT, related_name="ledger_entries")
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="search_credit_ledger_entries")
    search_run = models.ForeignKey("busquedas.SearchRun", on_delete=models.PROTECT, null=True, blank=True, related_name="commercial_credit_entries")
    idempotency_key = models.CharField(max_length=160, unique=True)
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount_credits = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=200, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(entry_type="adjustment") | models.Q(amount_credits__gte=0), name="search_credit_direction_valid"),
        ]

    def __str__(self):
        return f"{self.entry_type}: {self.amount_credits} ({self.idempotency_key})"


class DailyProductMetric(models.Model):
    class MetricName(models.TextChoices):
        REGISTRATION_COMPLETED = "registration_completed", "registration_completed"
        FIRST_SEARCH_CREATED = "first_search_created", "first_search_created"
        FIRST_CAPTURE_AVAILABLE = "first_capture_available", "first_capture_available"
        FIRST_OPPORTUNITY_CREATED = "first_opportunity_created", "first_opportunity_created"
        FIRST_DATED_NEXT_ACTION = "first_dated_next_action", "first_dated_next_action"
        CANONICAL_ACTIVATION_COMPLETED = "canonical_activation_completed", "canonical_activation_completed"
        TRIAL_EXPIRED = "trial_expired", "trial_expired"
        DEMO_REQUEST_PENDING = "demo_request_pending", "demo_request_pending"
        DEMO_REQUEST_NOTIFIED = "demo_request_notified", "demo_request_notified"
        DEMO_REQUEST_DELIVERY_FAILED = "demo_request_delivery_failed", "demo_request_delivery_failed"

    class DimensionName(models.TextChoices):
        ALL = "all", "all"
        SIGNUP_SOURCE = "signup_source", "signup_source"
        PROFILE_TYPE = "profile_type", "profile_type"

    natural_day = models.DateField()
    metric_name = models.CharField(max_length=40, choices=MetricName.choices)
    dimension_name = models.CharField(max_length=20, choices=DimensionName.choices)
    dimension_value = models.CharField(max_length=50)
    value = models.PositiveBigIntegerField()
    calculation_version = models.PositiveSmallIntegerField()
    calculated_at = models.DateTimeField()
    is_complete = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "natural_day", "metric_name", "dimension_name",
                    "dimension_value", "calculation_version",
                ],
                name="core_daily_metric_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["metric_name", "natural_day"],
                name="core_daily_metric_read",
            ),
        ]
