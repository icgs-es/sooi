from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def is_internal_admin(user):
    return bool(
        user
        and user.is_authenticated
        and (user.is_staff or user.is_superuser)
    )


def internal_admin_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_internal_admin(getattr(request, "user", None)):
            messages.warning(
                request,
                "La configuración interna de SOOI está reservada a administración."
            )
            return redirect("/app/")
        return view_func(request, *args, **kwargs)

    return _wrapped
