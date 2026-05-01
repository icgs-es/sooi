from celery import shared_task

from .models import SearchProfile, SearchRun
from .services import run_search_profile


@shared_task(bind=True, name="busquedas.run_search_profile_task")
def run_search_profile_task(self, search_profile_id: int, run_id: int | None = None) -> int | None:
    try:
        search_profile = SearchProfile.objects.get(pk=search_profile_id)
    except SearchProfile.DoesNotExist:
        return None

    run = None
    if run_id:
        try:
            run = SearchRun.objects.get(pk=run_id, search_profile=search_profile)
        except SearchRun.DoesNotExist:
            run = None

    result = run_search_profile(search_profile, run=run)
    return result.id
