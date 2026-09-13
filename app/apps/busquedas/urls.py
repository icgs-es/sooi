from django.urls import path

from .views import (
    searchprofile_create,
    searchprofile_detail,
    searchprofile_execute,
    searchprofile_hybrid_v261,
    searchprofile_list,
    searchprofile_pause,
    searchprofile_reactivate,
    searchprofile_close_empty,
    searchprofile_close_with_opportunity,
    searchprofile_update,
    searchrun_detail,
    searchrun_expand,
)

urlpatterns = [
    path("", searchprofile_list, name="searchprofile_list"),
    path("nuevo/", searchprofile_create, name="searchprofile_create"),
    path("<int:pk>/", searchprofile_detail, name="searchprofile_detail"),
    path("<int:pk>/editar/", searchprofile_update, name="searchprofile_update"),
    path("<int:pk>/ejecutar/", searchprofile_execute, name="searchprofile_execute"),
    path("<int:pk>/v261/", searchprofile_hybrid_v261, name="searchprofile_hybrid_v261"),
    path("ejecuciones/<int:pk>/", searchrun_detail, name="searchrun_detail"),
    path("ejecuciones/<int:pk>/ampliar/", searchrun_expand, name="searchrun_expand"),
    path("<int:pk>/pausar/", searchprofile_pause, name="searchprofile_pause"),
    path("<int:pk>/reactivar/", searchprofile_reactivate, name="searchprofile_reactivate"),
    path("<int:pk>/cerrar-desierta/", searchprofile_close_empty, name="searchprofile_close_empty"),
    path("<int:pk>/cerrar-con-oportunidad/<int:opportunity_pk>/", searchprofile_close_with_opportunity, name="searchprofile_close_with_opportunity"),
]
