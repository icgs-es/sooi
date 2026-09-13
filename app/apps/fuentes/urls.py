from apps.core.access import internal_admin_required
from django.urls import path

from .views import source_create, source_edit, source_list

urlpatterns = [
    path("fuentes/", internal_admin_required(source_list), name="source_list"),
    path("fuentes/nueva/", internal_admin_required(source_create), name="source_create"),
    path("fuentes/<int:pk>/editar/", internal_admin_required(source_edit), name="source_edit"),
]