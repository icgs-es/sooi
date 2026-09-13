from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import DemoRequest
from .observability import emit


def retry_demo_notification(demo_request_id, *, correlation_id=None):
    """Notify once successfully; failed calls are safe to retry by primary key."""
    with transaction.atomic():
        request = DemoRequest.objects.select_for_update().get(pk=demo_request_id)
        if request.delivery_status == DemoRequest.DeliveryStatus.SENT:
            return request

        request.notification_attempts += 1
        request.save(update_fields=["notification_attempts", "updated_at"])
        try:
            delivered = send_mail(
                subject=f"Nueva solicitud de demo SOOI · {request.name}",
                message=(
                    f"Nombre: {request.name}\nEmail: {request.email}\n"
                    f"Teléfono: {request.phone or '-'}\n"
                    f"Perfil: {request.get_profile_type_display()}\n"
                    f"Dominio: {request.source_domain}\n\n"
                    f"Mensaje:\n{request.message or '-'}\n"
                ),
                from_email=None,
                recipient_list=["info@sooi.io"],
                fail_silently=False,
            )
            if not delivered:
                raise RuntimeError("notification_not_accepted")
        except Exception as exc:
            request.delivery_status = DemoRequest.DeliveryStatus.FAILED
            request.last_notification_error = exc.__class__.__name__[:500]
            request.save(
                update_fields=[
                    "delivery_status", "notification_attempts",
                    "last_notification_error", "updated_at",
                ]
            )
            emit(
                "smtp_delivery", "failure", correlation_id,
                component="smtp", operation="demo_notice",
                reason_code="provider_error",
            )
            return request

        request.delivery_status = DemoRequest.DeliveryStatus.SENT
        request.last_notification_error = ""
        request.notified_at = timezone.now()
        request.save(
            update_fields=[
                "delivery_status", "notification_attempts",
                "last_notification_error", "notified_at", "updated_at",
            ]
        )
        emit(
            "smtp_delivery", "success", correlation_id,
            component="smtp", operation="demo_notice", reason_code="accepted",
        )
        return request
