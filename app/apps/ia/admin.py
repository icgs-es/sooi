from django.contrib import admin

from .models import AIProviderConfig


@admin.register(AIProviderConfig)
class AIProviderConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider_code",
        "model_name",
        "is_active",
        "is_default",
        "supports_web_search",
        "priority_order",
        "updated_at",
    )
    list_filter = (
        "provider_code",
        "is_active",
        "is_default",
        "supports_web_search",
        "supports_reasoning",
    )
    search_fields = ("name", "model_name", "base_url", "notes")
    ordering = ("priority_order", "name")

    fieldsets = (
        (
            "Proveedor",
            {
                "fields": (
                    "name",
                    "provider_code",
                    "model_name",
                    "base_url",
                    "api_key",
                )
            },
        ),
        (
            "Comportamiento",
            {
                "fields": (
                    "is_active",
                    "is_default",
                    "supports_web_search",
                    "supports_reasoning",
                    "priority_order",
                )
            },
        ),
        (
            "Notas",
            {
                "fields": ("notes",)
            },
        ),
    )