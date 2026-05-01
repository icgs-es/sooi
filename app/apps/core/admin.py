from django.contrib import admin

# Register your models here.


from .models import DemoRequest


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "profile_type", "status", "created_at")
    list_filter = ("status", "profile_type", "created_at")
    search_fields = ("name", "email", "phone", "message")
    readonly_fields = ("created_at", "updated_at")
