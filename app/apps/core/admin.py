from django.contrib import admin

# Register your models here.


from .models import (
    DailyDigestDelivery, DemoRequest, NotificationPreference,
    SearchCreditAccountPeriod, SearchCreditLedgerEntry, UserProfile,
)


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "profile_type", "status", "delivery_status", "notification_attempts", "created_at")
    list_filter = ("status", "delivery_status", "profile_type", "created_at")
    search_fields = ("name", "email", "phone", "message")
    readonly_fields = ("delivery_status", "notification_attempts", "last_notification_error", "notified_at", "created_at", "updated_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "company", "plan", "is_trial", "trial_start", "trial_end", "signup_source"]
    list_filter = ["plan", "is_trial", "signup_source"]
    search_fields = ["user__email", "company"]
    readonly_fields = ["trial_start", "trial_end"]


@admin.register(SearchCreditAccountPeriod)
class SearchCreditAccountPeriodAdmin(admin.ModelAdmin):
    list_display = ("owner", "period_start", "period_end", "allowance_credits", "plan_code")
    readonly_fields = ("owner", "period_start", "period_end", "allowance_credits", "plan_code", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SearchCreditLedgerEntry)
class SearchCreditLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "owner", "entry_type", "amount_credits", "search_run", "idempotency_key")
    list_filter = ("entry_type", "created_at")
    search_fields = ("idempotency_key", "owner__email")
    readonly_fields = (
        "account_period", "owner", "search_run", "idempotency_key", "entry_type",
        "amount_credits", "reason", "metadata", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("owner", "daily_digest_enabled", "daily_digest_time", "updated_at")
    list_filter = ("daily_digest_enabled", "daily_digest_time")
    search_fields = ("owner__username", "owner__email")


@admin.register(DailyDigestDelivery)
class DailyDigestDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "owner", "local_date", "channel", "status", "attempt_count",
        "scheduled_for", "sent_at", "updated_at",
    )
    list_filter = ("status", "channel", "local_date")
    search_fields = ("owner__username", "owner__email")
    readonly_fields = (
        "owner", "delivery_type", "channel", "local_date", "status",
        "scheduled_for", "attempted_at", "sent_at", "attempt_count",
        "next_retry_at", "reason_code", "last_error_class",
        "payload_fingerprint", "claim_token", "lease_expires_at",
        "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
