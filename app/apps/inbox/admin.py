from django.contrib import admin

from .models import EmailAccount, InboundEmail


@admin.register(EmailAccount)
class EmailAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "email_address", "provider_label", "is_active", "last_sync_at", "owner")
    list_filter = ("is_active", "provider_label")
    search_fields = ("name", "email_address", "imap_host", "imap_username")


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "from_email", "status", "account", "search_profile", "received_at", "owner")
    list_filter = ("status", "account")
    search_fields = ("subject", "from_email", "body_text", "snippet")
