from apps.core.access import internal_admin_required
from django.urls import include, path

from .views import (
    dashboard,
    demo_request,
    home,
    internal_user_create,
    internal_user_edit,
    internal_user_list,
    privacy_policy,
    registro_view,
    system_settings_edit,
    terms_of_use,
)

urlpatterns = [
    path("app/inbox/", include("apps.inbox.urls")),
    path("", home, name="home"),
    path("registro/", registro_view, name="registro"),
    path("solicitar-demo/", demo_request, name="demo_request"),
    path("privacidad/", privacy_policy, name="privacy_policy"),
    path("terminos/", terms_of_use, name="terms_of_use"),
    path("app/", dashboard, name="dashboard"),
    path("app/configuracion/", internal_admin_required(system_settings_edit), name="system_settings"),
    path("app/ia/", include("apps.ia.urls")),
    path("app/busquedas/", include("apps.busquedas.urls")),
    path("app/captacion/", include("apps.inmuebles.urls")),
    path("app/", include("apps.seguimiento.urls")),
    path("app/", include("apps.fuentes.urls")),
    path("app/usuarios/", internal_admin_required(internal_user_list), name="internal_user_list"),
    path("app/usuarios/nuevo/", internal_admin_required(internal_user_create), name="internal_user_create"),
    path("app/usuarios/<int:pk>/editar/", internal_admin_required(internal_user_edit), name="internal_user_edit"),
]
