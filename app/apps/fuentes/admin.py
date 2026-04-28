from django.contrib import admin

from .models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "source_type", "is_active", "base_url", "updated_at")
    list_filter = ("source_type", "is_active")
    search_fields = ("name", "code", "base_url")
    ordering = ("name",)