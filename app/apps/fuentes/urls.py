from django.urls import path

from .views import source_create, source_edit, source_list

urlpatterns = [
    path("fuentes/", source_list, name="source_list"),
    path("fuentes/nueva/", source_create, name="source_create"),
    path("fuentes/<int:pk>/editar/", source_edit, name="source_edit"),
]