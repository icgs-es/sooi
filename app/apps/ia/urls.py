from django.urls import path

from .views import ai_provider_create, ai_provider_edit, ai_provider_list

urlpatterns = [
    path("proveedores/", ai_provider_list, name="ai_provider_list"),
    path("proveedores/nuevo/", ai_provider_create, name="ai_provider_create"),
    path("proveedores/<int:pk>/editar/", ai_provider_edit, name="ai_provider_edit"),
]