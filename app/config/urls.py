from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
]