from celery import shared_task

from .models import SearchProfile, SearchRun
from .services import run_search_profile
from apps.core.observability import emit, new_correlation_id
from .commercial_metering import commercial_metering_enabled, settle_search_run

MAX_RETRIES = 3
NON_RETRYABLE_ERRORS = (PermissionError, TypeError, ValueError)


@shared_task(bind=True, name="busquedas.run_search_profile_task")
def run_search_profile_task(self, search_profile_id: int, run_id: int | None = None) -> int | None:
    correlation_id = new_correlation_id(getattr(self.request, "correlation_id", None))
    try:
        search_profile = SearchProfile.objects.get(pk=search_profile_id)
    except SearchProfile.DoesNotExist:
        emit("search_run", "failure", correlation_id, component="celery",
             operation="run_search", object_type="search_profile",
             object_id=search_profile_id, reason_code="not_found")
        return None

    run = None
    if run_id:
        try:
            run = SearchRun.objects.get(pk=run_id, search_profile=search_profile)
        except SearchRun.DoesNotExist:
            run = None

    if (
        run is not None and run.governance_enabled
        and run.status in {SearchRun.Status.COMPLETED, SearchRun.Status.COMPLETED_WITH_ERRORS}
        and isinstance(run.raw_response, dict)
        and isinstance(run.raw_response.get("source_coverage"), list)
    ):
        if commercial_metering_enabled():
            settle_search_run(run)
        return run.id

    try:
        result = run_search_profile(search_profile, run=run)
    except NON_RETRYABLE_ERRORS:
        emit("search_run", "failure", correlation_id, component="celery",
             operation="run_search", object_type="search_profile",
             object_id=search_profile_id, owner_id=search_profile.owner_id,
             reason_code="unexpected_error")
        raise
    except Exception as exc:
        retries = getattr(self.request, "retries", 0)
        exhausted = retries >= MAX_RETRIES
        emit("search_run", "failure" if exhausted else "retry", correlation_id, component="celery",
             operation="run_search", object_type="search_profile",
             object_id=search_profile_id, owner_id=search_profile.owner_id,
             attempt=retries + 1,
             reason_code="retry_exhausted" if exhausted else "provider_unavailable")
        if exhausted:
            if run is not None and commercial_metering_enabled() and run.governance_enabled:
                run.status = SearchRun.Status.FAILED
                run.save(update_fields=["status", "updated_at"])
                settle_search_run(run)
            raise
        raise self.retry(exc=exc, countdown=min(2 ** retries, 60), max_retries=MAX_RETRIES)
    if commercial_metering_enabled() and result.governance_enabled and result.status in {
        SearchRun.Status.COMPLETED, SearchRun.Status.COMPLETED_WITH_ERRORS, SearchRun.Status.FAILED,
    }:
        settle_search_run(result)
    state = "partial" if result.status == SearchRun.Status.COMPLETED_WITH_ERRORS else (
        "failure" if result.status == SearchRun.Status.FAILED else "success"
    )
    emit("search_run", state, correlation_id, component="celery",
         operation="run_search", object_type="search_run", object_id=result.id,
         owner_id=search_profile.owner_id, provider_state=result.status)
    return result.id
