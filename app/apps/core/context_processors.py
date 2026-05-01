from .models import SystemSettings
from apps.busquedas.models import SearchRun


def system_settings(request):
    try:
        settings_obj = SystemSettings.get_solo()
    except Exception:
        settings_obj = None

    return {
        "system_settings": settings_obj,
    }

def active_ai_search_runs(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {
            "active_ai_runs": [],
            "active_ai_runs_count": 0,
        }

    runs = list(
        SearchRun.objects.select_related("search_profile")
        .filter(
            search_profile__owner=request.user,
            status__in=[
                SearchRun.Status.PENDING,
                SearchRun.Status.RUNNING,
            ],
        )
        .order_by("-created_at")[:3]
    )

    return {
        "active_ai_runs": runs,
        "active_ai_runs_count": len(runs),
    }
