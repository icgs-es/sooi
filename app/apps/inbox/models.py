from django.conf import settings
from django.db import models

from apps.busquedas.models import SearchProfile
from apps.inmuebles.models import CapturedProperty


class EmailAccount(models.Model):
    name = models.CharField("nombre", max_length=120)
    email_address = models.EmailField("email")
    provider_label = models.CharField("proveedor", max_length=80, blank=True)

    imap_host = models.CharField("host IMAP", max_length=180, blank=True)
    imap_port = models.PositiveIntegerField("puerto IMAP", default=993)
    imap_use_ssl = models.BooleanField("usar SSL", default=True)
    imap_username = models.CharField("usuario IMAP", max_length=180, blank=True)
    imap_password = models.CharField("contraseña IMAP", max_length=255, blank=True)

    is_active = models.BooleanField("activo", default=True)
    last_sync_at = models.DateTimeField("última sincronización", null=True, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_accounts",
        verbose_name="propietario",
    )

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Cuenta de email"
        verbose_name_plural = "Cuentas de email"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["owner", "is_active"]),
            models.Index(fields=["email_address"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.email_address}"


class InboundEmail(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Nuevo"
        REVIEWED = "reviewed", "Revisado"
        CONVERTED = "converted", "Convertido en captación"
        DISCARDED = "discarded", "Descartado"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inbound_emails",
        verbose_name="propietario",
    )
    account = models.ForeignKey(
        EmailAccount,
        on_delete=models.CASCADE,
        related_name="emails",
        verbose_name="cuenta",
    )
    search_profile = models.ForeignKey(
        SearchProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_emails",
        verbose_name="expediente/búsqueda",
    )
    captured_property = models.ForeignKey(
        CapturedProperty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_emails",
        verbose_name="captación creada",
    )

    status = models.CharField("estado", max_length=20, choices=Status.choices, default=Status.NEW)

    message_uid = models.CharField("UID mensaje", max_length=180, blank=True)
    message_id = models.CharField("Message-ID", max_length=255, blank=True)

    subject = models.CharField("asunto", max_length=255, blank=True)
    from_name = models.CharField("nombre remitente", max_length=180, blank=True)
    from_email = models.EmailField("email remitente", blank=True)
    received_at = models.DateTimeField("recibido", null=True, blank=True)

    snippet = models.TextField("resumen", blank=True)
    body_text = models.TextField("cuerpo texto", blank=True)

    detected_urls = models.JSONField("URLs detectadas", default=list, blank=True)
    raw_metadata = models.JSONField("metadatos", default=dict, blank=True)

    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "Email entrante"
        verbose_name_plural = "Emails entrantes"
        ordering = ["-received_at", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["account", "message_uid"]),
            models.Index(fields=["received_at"]),
        ]

    def __str__(self) -> str:
        return self.subject or f"Email #{self.pk}"
