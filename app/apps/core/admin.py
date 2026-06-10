from django.contrib import admin

# Register your models here.


from .models import DemoRequest, UserProfile


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "profile_type", "status", "created_at")
    list_filter = ("status", "profile_type", "created_at")
    search_fields = ("name", "email", "phone", "message")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "company", "is_trial", "trial_start", "trial_end", "signup_source"]
    list_filter = ["is_trial", "signup_source"]
    search_fields = ["user__email", "company"]
    readonly_fields = ["trial_start", "trial_end"]
