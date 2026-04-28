from django.urls import path

from .views import (
    capturedproperty_convert_to_opportunity,
    capturedproperty_delete,
    capturedproperty_detail,
    capturedproperty_list,
    capturedproperty_edit,
    capturedproperty_mark_in_review,
    capturedproperty_mark_interesting,
    capturedproperty_manual_create,
)

urlpatterns = [
    path("", capturedproperty_list, name="capturedproperty_list"),
    path("<int:pk>/", capturedproperty_detail, name="capturedproperty_detail"),
    path("<int:pk>/marcar-interesante/", capturedproperty_mark_interesting, name="capturedproperty_mark_interesting"),
    path("<int:pk>/marcar-revision/", capturedproperty_mark_in_review, name="capturedproperty_mark_in_review"),
    path("<int:pk>/convertir/", capturedproperty_convert_to_opportunity, name="capturedproperty_convert_to_opportunity"),
    path("<int:pk>/eliminar/", capturedproperty_delete, name="capturedproperty_delete"),
    path("<int:pk>/editar/", capturedproperty_edit, name="capturedproperty_edit"),
    path("manual/", capturedproperty_manual_create, name="capturedproperty_manual_create"),
]