from django.contrib import admin

from .models import SearchProfile, SearchRun


@admin.register(SearchProfile)
class SearchProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "province", "max_price", "min_bedrooms", "is_active", "updated_at")
    list_filter = ("is_active", "province")
    search_fields = ("name", "province")
    ordering = ("name",)


@admin.register(SearchRun)
class SearchRunAdmin(admin.ModelAdmin):
    list_display = (
        "search_profile",
        "execution_mode",
        "provider",
        "model_name",
        "status",
        "total_candidates",
        "total_valid_candidates",
        "total_found",
        "total_new",
        "total_updated",
        "total_errors",
        "created_at",
    )
    list_filter = ("status", "execution_mode", "provider", "created_at")
    search_fields = ("search_profile__name", "provider", "model_name", "query_text")
    ordering = ("-created_at",)