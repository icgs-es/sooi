from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity
from apps.core.plans import get_user_plan, get_max_active_searches
from apps.core.demo import get_demo_status
from apps.ia.usage import can_run_ai_discovery, format_ai_quota_message, get_ai_usage_summary

from .forms import SearchProfileForm
from .models import SearchProfile, SearchRun
from .services_hybrid_coverage_v261 import run_hybrid_discovery_v261
from .tasks import run_search_profile_task
from .search_budget_ux import customer_run_status, get_searchrun_list_metrics, mode_previews, run_customer_context, validate_authorization
from .searchrun_governance import initialize_search_run_governance, search_governance_enabled
from .commercial_metering import commercial_metering_enabled, reserve_search_run, settle_search_run


MAX_ACTIVE_SEARCHES = 6
ACTIVE_SEARCH_STATUSES = [
    SearchProfile.Status.ACTIVE,
    SearchProfile.Status.PAUSED,
]


def get_active_searches_qs(user):
    return SearchProfile.objects.filter(
        owner=user,
        status__in=ACTIVE_SEARCH_STATUSES,
    )


def get_next_available_color(user):
    used_colors = set(
        get_active_searches_qs(user)
        .exclude(color="")
        .values_list("color", flat=True)
    )

    for value, _label in SearchProfile.Color.choices:
        if value not in used_colors:
            return value

    return ""


@login_required
def searchprofile_list(request):
    active_search_profiles = (
        SearchProfile.objects
        .filter(
            owner=request.user,
            status__in=[
                SearchProfile.Status.ACTIVE,
                SearchProfile.Status.PAUSED,
            ],
        )
        .order_by("status", "name")
    )

    historical_search_profiles = (
        SearchProfile.objects
        .filter(owner=request.user)
        .exclude(
            status__in=[
                SearchProfile.Status.ACTIVE,
                SearchProfile.Status.PAUSED,
            ]
        )
        .order_by("-closed_at", "-updated_at")
    )

    active_searches_count = get_active_searches_qs(request.user).count()
    user_plan = get_user_plan(request.user)
    ai_usage = get_ai_usage_summary(request.user)
    if not ai_usage.get("is_commercial_metering"):
        ai_usage["ai_discovery_remaining"] = (
            ai_usage["credits_remaining"] // ai_usage["ai_discovery_cost"]
            if ai_usage["ai_discovery_cost"] else 0
        )
    demo_status = get_demo_status(request.user)

    recent_runs = (
        SearchRun.objects.select_related("search_profile")
        .filter(search_profile__owner=request.user)
        .order_by("-created_at")[:10]
    )
    for run in recent_runs:
        run.customer_status = customer_run_status(run)
        run.list_metrics = get_searchrun_list_metrics(run)

    return render(
        request,
        "busquedas/searchprofile_list.html",
        {
            "active_search_profiles": active_search_profiles,
            "historical_search_profiles": historical_search_profiles,
            "recent_runs": recent_runs,
            "active_searches_count": active_searches_count,
            "max_active_searches": get_max_active_searches(request.user),
            "user_plan": user_plan,
            "ai_usage": ai_usage,
            "demo_status": demo_status,
            "search_governance_enabled": search_governance_enabled(),
        },
    )


@login_required
def searchprofile_detail(request, pk):
    profile = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    runs = profile.runs.all().order_by("-created_at")[:10]
    for run in runs:
        run.customer_status = customer_run_status(run)
        raw = run.raw_response if isinstance(run.raw_response, dict) else {}
        quality = raw.get("quality_semantics")
        if (not isinstance(quality, dict) or "actionable_count" not in quality) and raw.get("source_coverage"):
            from .search_quality_semantics import evaluate_search_quality_semantics
            quality = evaluate_search_quality_semantics(raw)
        quality = quality or {}
        run.customer_valid_count = int(quality.get("actionable_count", run.total_valid_candidates) or 0)
        run.customer_review_count = int(quality.get("review_required_count", 0) or 0)
        run.customer_new_count = min(run.total_new, run.customer_valid_count)

    captured_qs = (
        CapturedProperty.objects
        .select_related("source", "search_profile")
        .filter(owner=request.user, search_profile=profile)
        .order_by("-captured_at")
    )

    opportunities_qs = (
        PropertyOpportunity.objects
        .select_related("captured_property", "broker_company", "main_contact", "search_profile")
        .filter(owner=request.user, search_profile=profile)
        .order_by("-updated_at", "-created_at")
    )

    from .geography_runtime import geography_runtime_enabled, resolve_profile_geography
    geography_runtime = resolve_profile_geography(profile) if geography_runtime_enabled() else None
    return render(
        request,
        "busquedas/searchprofile_detail.html",
        {
            "profile": profile,
            "runs": runs,
            "captured_properties": captured_qs[:10],
            "opportunities": opportunities_qs[:10],
            "captured_count": captured_qs.count(),
            "opportunities_count": opportunities_qs.count(),
            "runs_count": profile.runs.count(),
            "search_governance_enabled": search_governance_enabled(),
            "governed_modes": mode_previews(profile, request.user) if search_governance_enabled() else [],
            "geography_runtime": geography_runtime,
        },
    )


@login_required
def searchprofile_create(request):
    active_count = get_active_searches_qs(request.user).count()
    user_plan = get_user_plan(request.user)
    max_active_searches = get_max_active_searches(request.user)

    if active_count >= max_active_searches:
        messages.warning(
            request,
            f"Tu plan {user_plan['name']} permite hasta {max_active_searches} búsquedas activas o pausadas. "
            "Cierra una búsqueda antes de crear otra o mejora tu plan."
        )
        return redirect("searchprofile_list")

    if request.method == "POST":
        form = SearchProfileForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.status = SearchProfile.Status.ACTIVE
            obj.is_active = True
            obj.automation_enabled = False
            obj.color = get_next_available_color(request.user)
            obj.save()
            form.save_m2m()
            messages.success(request, "Búsqueda creada correctamente.")
            return redirect("searchprofile_list")
    else:
        form = SearchProfileForm()

    return render(
        request,
        "busquedas/searchprofile_form.html",
        {
            "form": form,
            "section_title": "Nueva búsqueda",
            "submit_label": "Guardar búsqueda",
            "active_searches_count": active_count,
            "max_active_searches": max_active_searches,
        },
    )


@login_required
def searchprofile_update(request, pk):
    obj = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if request.method == "POST":
        form = SearchProfileForm(request.POST, instance=obj)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.owner = request.user
            updated.save()
            form.save_m2m()
            messages.success(request, "Búsqueda actualizada correctamente.")
            return redirect("searchprofile_detail", pk=obj.pk)
    else:
        form = SearchProfileForm(instance=obj)

    return render(
        request,
        "busquedas/searchprofile_form.html",
        {
            "form": form,
            "section_title": "Editar búsqueda",
            "submit_label": "Guardar cambios",
            "search_profile": obj,
        },
    )


@login_required
@require_POST
def searchprofile_execute(request, pk):
    obj = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if obj.status != SearchProfile.Status.ACTIVE:
        messages.warning(request, "Solo se pueden ejecutar búsquedas activas.")
        return redirect("searchprofile_detail", pk=obj.pk)

    governed = search_governance_enabled()
    if governed:
        mode = request.POST.get("search_mode")
        budget = request.POST.get("budget_max_credits")
        try:
            budget = validate_authorization(request.user, mode, budget)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("searchprofile_detail", pk=obj.pk)
    else:
        allowed, usage = can_run_ai_discovery(request.user)
        if not allowed:
            messages.warning(request, format_ai_quota_message(usage))
            return redirect("searchprofile_detail", pk=obj.pk)

    try:
        from .geography_runtime import geography_snapshot_for_profile, runtime_search_locations
        geography_snapshot = geography_snapshot_for_profile(obj)
        with transaction.atomic():
            run = SearchRun.objects.create(
                search_profile=obj,
                status=SearchRun.Status.PENDING,
                execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                started_at=timezone.now(),
                filters_snapshot={
            "operation_type": obj.operation_type,
            "province": obj.province,
            "zone": obj.zone or "",
            "geography_scope": obj.geography_scope or "legacy",
            "search_locations": runtime_search_locations(obj),
            "geographic_area_id": obj.geographic_area_id,
            "property_types": obj.property_types or [],
            "min_price": str(obj.min_price) if obj.min_price is not None else None,
            "max_price": str(obj.max_price) if obj.max_price is not None else None,
            "min_area_m2": str(obj.min_area_m2) if obj.min_area_m2 is not None else None,
            "min_bedrooms": obj.min_bedrooms,
            "ai_prompt": obj.ai_prompt or "",
            **geography_snapshot,
                },
            )

            if governed:
                initialize_search_run_governance(
                    run, run.filters_snapshot, search_mode=mode, budget_max_credits=budget,
                )
                if commercial_metering_enabled():
                    reserve_search_run(run, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("searchprofile_detail", pk=obj.pk)

    run_search_profile_task.delay(obj.pk, run.id)

    messages.success(
        request,
        "Exploración IA lanzada en segundo plano. Puedes seguir usando SOOI mientras se completa.",
    )
    return redirect("searchprofile_detail", pk=obj.pk)


@login_required
def searchrun_detail(request, pk):
    run = get_object_or_404(
        SearchRun.objects.select_related("search_profile", "reused_from_search_run"),
        pk=pk, search_profile__owner=request.user,
    )
    context = run_customer_context(run)
    snapshot = run.filters_snapshot if isinstance(run.filters_snapshot, dict) else {}
    context["geography_snapshot"] = snapshot if snapshot.get("geography_runtime_contract") else None
    context["search_governance_enabled"] = search_governance_enabled()
    if context["can_expand"]:
        context["target_preview"] = next(
            item for item in mode_previews(run.search_profile, request.user)
            if item["mode"] == context["next_mode"]
        )
    return render(request, "busquedas/searchrun_detail.html", context)


@login_required
@require_POST
def searchrun_expand(request, pk):
    run = get_object_or_404(SearchRun.objects.select_related("search_profile"), pk=pk, search_profile__owner=request.user)
    if not search_governance_enabled() or not run.governance_enabled:
        return redirect("searchrun_detail", pk=run.pk)
    target = request.POST.get("search_mode")
    order = ("free", "eco", "amplia", "profunda")
    try:
        if order.index(target) <= order.index(run.search_mode):
            raise ValidationError("El modo de ampliación debe ser estrictamente más amplio.")
        budget = validate_authorization(request.user, target, request.POST.get("budget_max_credits"))
    except (ValueError, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("searchrun_detail", pk=run.pk)
    try:
        with transaction.atomic():
            from .geography_runtime import geography_snapshot_for_profile, runtime_search_locations
            profile_snapshot = {
                "operation_type": run.search_profile.operation_type,
                "province": run.search_profile.province,
                "geography_scope": run.search_profile.geography_scope or "legacy",
                "search_locations": runtime_search_locations(run.search_profile),
                **geography_snapshot_for_profile(run.search_profile),
            }
            new_run = SearchRun.objects.create(
                search_profile=run.search_profile, status=SearchRun.Status.PENDING,
                execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY, started_at=timezone.now(),
                filters_snapshot=profile_snapshot,
            )
            initialize_search_run_governance(new_run, profile_snapshot, search_mode=target, budget_max_credits=budget)
            if commercial_metering_enabled():
                reserve_search_run(new_run, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("searchrun_detail", pk=run.pk)
    run_search_profile_task.delay(run.search_profile_id, new_run.id)
    messages.success(request, "Ampliación autorizada. Se ha creado una nueva ejecución gobernada.")
    return redirect("searchrun_detail", pk=new_run.pk)

@login_required
def searchprofile_hybrid_v261(request, pk):
    profile = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    def _bounded_int(value, default, min_value, max_value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(min_value, min(max_value, parsed))

    timeout = _bounded_int(
        request.POST.get("timeout") or request.GET.get("timeout"),
        12,
        4,
        30,
    )
    max_results = _bounded_int(
        request.POST.get("max_results") or request.GET.get("max_results"),
        8,
        1,
        20,
    )
    include_ai = (
        request.POST.get("include_ai") == "1"
        or request.GET.get("include_ai") == "1"
    )

    result = None

    if request.method == "POST":
        action = request.POST.get("action")

        if profile.status != SearchProfile.Status.ACTIVE:
            messages.error(request, "Solo se puede ejecutar V2.6.1 sobre búsquedas activas.")
            return redirect("searchprofile_detail", pk=profile.pk)

        if action == "analyze":
            if search_governance_enabled():
                mode = request.POST.get("search_mode")
                try:
                    budget = validate_authorization(
                        request.user, mode, request.POST.get("budget_max_credits"),
                    )
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                    return redirect("searchprofile_hybrid_v261", pk=profile.pk)
                try:
                    with transaction.atomic():
                        run = SearchRun.objects.create(
                            search_profile=profile, status=SearchRun.Status.RUNNING,
                            execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                            started_at=timezone.now(),
                        )
                        initialize_search_run_governance(
                            run, profile,
                            search_mode=mode, budget_max_credits=budget,
                        )
                        if commercial_metering_enabled():
                            reserve_search_run(run, request.user)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                    return redirect("searchprofile_hybrid_v261", pk=profile.pk)
                from .services import _run_hybrid_discovery_v2614
                run = _run_hybrid_discovery_v2614(
                    profile, run=run, write_override=False,
                    timeout_override=timeout, max_results_override=max_results,
                    use_ai_override=mode != SearchRun.SearchMode.FREE,
                )
                if commercial_metering_enabled():
                    settle_search_run(run)
                result = run.raw_response
                result["analysis_run_id"] = run.pk
                request.session[f"hybrid_analysis_run:{profile.pk}"] = run.pk
            else:
                result = run_hybrid_discovery_v261(
                    profile_id=profile.pk, timeout=timeout,
                    max_results_per_source=max_results, use_ai=include_ai, write=False,
                )
            messages.info(request, "Análisis V2.6.1 ejecutado en modo dry-run. No se ha escrito en BD.")

        elif action == "write":
            confirm = (request.POST.get("confirm_write") or "").strip().upper()
            if confirm != "ESCRIBIR":
                messages.error(request, "Para escribir debes confirmar escribiendo ESCRIBIR.")
            else:
                if search_governance_enabled():
                    from .search_reuse import write_analysis
                    try:
                        result = write_analysis(
                            request.POST.get("analysis_run_id")
                            or request.session.get(f"hybrid_analysis_run:{profile.pk}"),
                            request.user,
                            request.POST.get("search_fingerprint") or None,
                        )
                    except (TypeError, ValueError, ValidationError):
                        messages.error(request, "El análisis es inválido, incompatible o ha caducado.")
                        result = None
                else:
                    result = run_hybrid_discovery_v261(
                        profile_id=profile.pk, timeout=timeout,
                        max_results_per_source=max_results, use_ai=include_ai, write=True,
                    )
                if result is not None:
                    wr = result.get("write_result") or {}
                    messages.success(
                        request,
                        "V2.6.1 escritura controlada ejecutada: "
                        f"creadas={wr.get('created', 0)}, "
                        f"actualizadas={wr.get('updated', 0)}, "
                        f"omitidas={wr.get('skipped', 0)}."
                    )
        else:
            messages.error(request, "Acción V2.6.1 no reconocida.")

    return render(
        request,
        "busquedas/searchprofile_hybrid_v261.html",
        {
            "profile": profile,
            "result": result,
            "timeout": timeout,
            "max_results": max_results,
            "include_ai": include_ai,
            "search_governance_enabled": search_governance_enabled(),
            "governed_modes": mode_previews(profile, request.user) if search_governance_enabled() else [],
        },
    )

@require_POST
def searchprofile_pause(request, pk):
    obj = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if obj.status != SearchProfile.Status.ACTIVE:
        messages.warning(request, "Solo se pueden pausar búsquedas activas.")
        return redirect("searchprofile_detail", pk=obj.pk)

    obj.status = SearchProfile.Status.PAUSED
    obj.is_active = True
    obj.save(update_fields=["status", "is_active", "updated_at"])

    messages.success(request, "Búsqueda pausada correctamente.")
    return redirect("searchprofile_detail", pk=obj.pk)


@login_required
@require_POST
def searchprofile_reactivate(request, pk):
    obj = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if obj.status != SearchProfile.Status.PAUSED:
        messages.warning(request, "Solo se pueden reactivar búsquedas pausadas.")
        return redirect("searchprofile_detail", pk=obj.pk)

    obj.status = SearchProfile.Status.ACTIVE
    obj.is_active = True
    obj.save(update_fields=["status", "is_active", "updated_at"])

    messages.success(request, "Búsqueda reactivada correctamente.")
    return redirect("searchprofile_detail", pk=obj.pk)


@login_required
@require_POST
def searchprofile_close_empty(request, pk):
    obj = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if obj.status not in [SearchProfile.Status.ACTIVE, SearchProfile.Status.PAUSED]:
        messages.warning(request, "Esta búsqueda ya no está activa.")
        return redirect("searchprofile_detail", pk=obj.pk)

    obj.status = SearchProfile.Status.CLOSED_EMPTY
    obj.is_active = False
    obj.closed_at = timezone.now()
    obj.color = ""
    if not obj.outcome_notes:
        obj.outcome_notes = "Búsqueda cerrada como desierta."
    obj.save(update_fields=["status", "is_active", "closed_at", "color", "outcome_notes", "updated_at"])

    messages.success(request, "Búsqueda cerrada como desierta. El color y la plaza activa quedan liberados.")
    return redirect("searchprofile_list")


@login_required
@require_POST
def searchprofile_close_with_opportunity(request, pk, opportunity_pk):
    profile = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    if profile.status not in [SearchProfile.Status.ACTIVE, SearchProfile.Status.PAUSED]:
        messages.warning(request, "Esta búsqueda ya no está activa.")
        return redirect("searchprofile_detail", pk=profile.pk)

    opportunity = get_object_or_404(
        PropertyOpportunity,
        pk=opportunity_pk,
        owner=request.user,
        search_profile=profile,
    )

    profile.status = SearchProfile.Status.CLOSED_WITH_OPPORTUNITY
    profile.is_active = False
    profile.closed_at = timezone.now()
    profile.selected_opportunity = opportunity
    profile.color = ""
    profile.outcome_notes = f"Búsqueda cerrada con oportunidad seleccionada: {opportunity.title}"
    profile.save(
        update_fields=[
            "status",
            "is_active",
            "closed_at",
            "selected_opportunity",
            "color",
            "outcome_notes",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Búsqueda cerrada con oportunidad seleccionada. El histórico queda conservado y se libera la plaza activa.",
    )
    return redirect("searchprofile_detail", pk=profile.pk)
