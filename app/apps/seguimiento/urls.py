from django.urls import path

from .views import (
    alert_detail,
    alert_list_create,
    alert_delete,
    alert_edit,
    opportunity_detail,
    opportunity_edit,
    opportunity_list,
    opportunity_delete,
    opportunity_add_activity,
    task_detail,
    task_list,
    task_delete,
    broker_company_list_create,
    opportunity_contact_list_create,
)

urlpatterns = [
    path("oportunidades/", opportunity_list, name="opportunity_list"),
    path("oportunidades/<int:pk>/", opportunity_detail, name="opportunity_detail"),
    path("oportunidades/<int:pk>/editar/", opportunity_edit, name="opportunity_edit"),
    path("oportunidades/<int:pk>/actividad/", opportunity_add_activity, name="opportunity_add_activity"),
    path("oportunidades/<int:pk>/eliminar/", opportunity_delete, name="opportunity_delete"),
    path("contactos-oportunidad/", opportunity_contact_list_create, name="opportunity_contact_list"),
    path("tareas/", task_list, name="task_list"),
    path("tareas/<int:pk>/", task_detail, name="task_detail"),
    path("tareas/<int:pk>/eliminar/", task_delete, name="task_delete"),
    path("alertas/", alert_list_create, name="alert_list"),
    path("alertas/<int:pk>/", alert_detail, name="alert_detail"),
    path("alertas/<int:pk>/eliminar/", alert_delete, name="alert_delete"),
    path("alertas/<int:pk>/editar/", alert_edit, name="alert_edit"),
    path("comercializadoras/", broker_company_list_create, name="broker_company_list"),
]