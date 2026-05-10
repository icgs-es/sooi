from django.urls import path

from .views import email_account_list_create, inbox_convert_to_capture, inbox_detail, inbox_discard, inbox_list, inbox_sync

urlpatterns = [
    path("", inbox_list, name="inbox_list"),
    path("sincronizar/", inbox_sync, name="inbox_sync"),
    path("cuentas/", email_account_list_create, name="email_account_list"),
    path("emails/<int:pk>/", inbox_detail, name="inbox_detail"),
    path("emails/<int:pk>/descartar/", inbox_discard, name="inbox_discard"),
    path("emails/<int:pk>/convertir-captacion/", inbox_convert_to_capture, name="inbox_convert_to_capture"),
]
