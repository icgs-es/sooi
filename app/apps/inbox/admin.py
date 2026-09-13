from django.contrib import admin

from .models import EmailAccount, InboundEmail


@admin.register(EmailAccount)
class EmailAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "email_address", "provider_label", "is_active", "last_sync_at", "owner")
    list_filter = ("is_active", "provider_label")
    search_fields = ("name", "email_address", "imap_host", "imap_username")
    def has_add_permission(self, request):
        return request.user.is_superuser

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(owner=request.user)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or request.user.is_superuser or obj.owner_id == request.user.id
        )

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or request.user.is_superuser or obj.owner_id == request.user.id
        )


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "from_email", "status", "account", "search_profile", "received_at", "owner")
    list_filter = ("status", "account")
    search_fields = ("subject", "from_email", "body_text", "snippet")

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(owner=request.user)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or request.user.is_superuser or obj.owner_id == request.user.id
        )

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or request.user.is_superuser or obj.owner_id == request.user.id
        )
