from apps.core.access import internal_admin_required
from django.urls import path

from .views import ai_provider_create, ai_provider_edit, ai_provider_list

urlpatterns = [
    path("proveedores/", internal_admin_required(ai_provider_list), name="ai_provider_list"),
    path("proveedores/nuevo/", internal_admin_required(ai_provider_create), name="ai_provider_create"),
    path("proveedores/<int:pk>/editar/", internal_admin_required(ai_provider_edit), name="ai_provider_edit"),
]