from django.contrib import admin

from .models import Alert, BrokerCompany, FollowUpTask, OpportunityContact, PropertyOpportunity


@admin.register(BrokerCompany)
class BrokerCompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "updated_at")
    search_fields = ("name", "phone", "email")
    ordering = ("name",)


@admin.register(OpportunityContact)
class OpportunityContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "role", "phone", "email", "preferred_channel", "updated_at")
    list_filter = ("role", "preferred_channel")
    search_fields = ("full_name", "phone", "email")
    ordering = ("full_name",)

@admin.register(PropertyOpportunity)
class PropertyOpportunityAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "priority",
        "asking_price_current",
        "target_price_internal",
        "next_action_type",
        "next_review_at",
        "updated_at",
    )
    list_filter = ("status", "priority", "next_action_type")
    search_fields = (
        "title",
        "captured_property__title",
        "next_action_notes",
        "province",
        "municipality",
    )
    ordering = ("-updated_at", "-created_at")

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("title", "alert_type", "severity", "status", "created_at")
    list_filter = ("alert_type", "severity", "status")
    search_fields = ("title", "message")
    ordering = ("-created_at",)


@admin.register(FollowUpTask)
class FollowUpTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "task_type", "status", "priority", "due_date", "updated_at")
    list_filter = ("task_type", "status", "priority")
    search_fields = ("title", "description")
    ordering = ("-created_at",)