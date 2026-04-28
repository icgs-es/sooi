from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity

from .forms import SearchProfileForm
from .models import SearchProfile, SearchRun
from .services import run_search_profile


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

    recent_runs = (
        SearchRun.objects.select_related("search_profile")
        .filter(search_profile__owner=request.user)
        .order_by("-created_at")[:10]
    )

    return render(
        request,
        "busquedas/searchprofile_list.html",
        {
            "active_search_profiles": active_search_profiles,
            "historical_search_profiles": historical_search_profiles,
            "recent_runs": recent_runs,
            "active_searches_count": active_searches_count,
            "max_active_searches": MAX_ACTIVE_SEARCHES,
        },
    )


@login_required
def searchprofile_detail(request, pk):
    profile = get_object_or_404(SearchProfile, pk=pk, owner=request.user)

    runs = profile.runs.all().order_by("-created_at")[:10]

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
        },
    )


@login_required
def searchprofile_create(request):
    active_count = get_active_searches_qs(request.user).count()

    if active_count >= MAX_ACTIVE_SEARCHES:
        messages.warning(
            request,
            f"Ya tienes {MAX_ACTIVE_SEARCHES} búsquedas activas o pausadas. "
            "Cierra una búsqueda antes de crear otra."
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
            "max_active_searches": MAX_ACTIVE_SEARCHES,
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
    run = run_search_profile(obj)
    messages.success(
        request,
        f"Búsqueda ejecutada: {run.total_found} captaciones procesadas, {run.total_new} nuevas.",
    )
    return redirect("capturedproperty_list")

@login_required
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
