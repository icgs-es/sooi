from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import EmailAccount, InboundEmail


@transaction.atomic
def reassign_inbound_email(*, message_id, target_account_id, actor):
    """Explicit superuser-only reassignment preserving ownership invariants."""
    if not actor.is_authenticated or not actor.is_superuser:
        raise PermissionDenied("Sólo un superusuario puede reasignar mensajes.")
    message = InboundEmail.objects.select_for_update().get(pk=message_id)
    target = EmailAccount.objects.select_for_update().get(pk=target_account_id)
    if message.captured_property_id or message.status == InboundEmail.Status.CONVERTED:
        raise ValidationError("Un mensaje convertido no se puede reasignar.")
    message.account = target
    message.owner = target.owner
    if message.search_profile_id and message.search_profile.owner_id != target.owner_id:
        message.search_profile = None
    message.save(update_fields=["account", "owner", "search_profile", "updated_at"])
    return message
