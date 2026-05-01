from django.urls import include, path

from .views import (
    dashboard,
    home,
    privacy_policy,
    terms_of_use,
    internal_user_create,
    internal_user_edit,
    internal_user_list,
    system_settings_edit,
)

urlpatterns = [
    path("", home, name="home"),
    path("privacidad/", privacy_policy, name="privacy_policy"),
    path("terminos/", terms_of_use, name="terms_of_use"),
    path("app/", dashboard, name="dashboard"),
    path("app/configuracion/", system_settings_edit, name="system_settings"),
    path("app/ia/", include("apps.ia.urls")),
    path("app/busquedas/", include("apps.busquedas.urls")),
    path("app/captacion/", include("apps.inmuebles.urls")),
    path("app/", include("apps.seguimiento.urls")),
    path("app/", include("apps.fuentes.urls")),
    path("app/usuarios/", internal_user_list, name="internal_user_list"),
    path("app/usuarios/nuevo/", internal_user_create, name="internal_user_create"),
    path("app/usuarios/<int:pk>/editar/", internal_user_edit, name="internal_user_edit"),
]