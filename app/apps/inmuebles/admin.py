from django.contrib import admin

from .models import CapturedProperty


@admin.register(CapturedProperty)
class CapturedPropertyAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "operation_type",
        "property_type",
        "province",
        "municipality",
        "price",
        "status",
        "review_status",
        "is_interesting",
        "captured_at",
    )
    list_filter = (
        "status",
        "review_status",
        "is_interesting",
        "operation_type",
        "property_type",
        "source",
        "province",
    )
    search_fields = (
        "title",
        "municipality",
        "province",
        "source_url",
        "source_external_id",
    )
    ordering = ("-captured_at",)