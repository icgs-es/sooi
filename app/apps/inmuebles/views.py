from django.contrib import messages
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.busquedas.models import SearchProfile
from apps.fuentes.models import Source
from apps.seguimiento.models import OpportunityActivity, PropertyOpportunity
from .forms import CapturedPropertyManualForm
from .models import CapturedProperty


@login_required
def capturedproperty_list(request):
    request.session["capturedproperty_last_list_url"] = request.get_full_path()

    status = request.GET.get("status", "").strip()
    operation_type = request.GET.get("operation_type", "").strip()
    property_type = request.GET.get("property_type", "").strip()
    source_id = request.GET.get("source_id", "").strip()
    entry_mode = request.GET.get("entry_mode", "").strip()
    location_query = request.GET.get("location", "").strip()
    search_profile_id = request.GET.get("search_profile_id", "").strip()

    qs = (
        CapturedProperty.objects.select_related("source", "search_profile")
        .filter(owner=request.user)
        .order_by("-captured_at")
    )

    if status:
        qs = qs.filter(status=status)

    if operation_type:
        qs = qs.filter(operation_type=operation_type)

    if property_type:
        qs = qs.filter(property_type=property_type)

    if source_id:
        qs = qs.filter(source_id=source_id)

    if location_query:
        qs = qs.filter(
            Q(municipality__icontains=location_query)
            | Q(province__icontains=location_query)
            | Q(zone_text__icontains=location_query)
            | Q(title__icontains=location_query)
        )

    if entry_mode:
        qs = qs.filter(entry_mode=entry_mode)

    if search_profile_id:
        qs = qs.filter(search_profile_id=search_profile_id)

    items = list(qs)
    for obj in items:
        try:
            obj.existing_opportunity = obj.opportunity
        except PropertyOpportunity.DoesNotExist:
            obj.existing_opportunity = None

    available_sources = (
        Source.objects.filter(captured_properties__owner=request.user)
        .distinct()
        .order_by("name")
    )

    available_search_profiles = (
        SearchProfile.objects.filter(owner=request.user)
        .order_by("status", "name")
    )

    return render(
        request,
        "inmuebles/capturedproperty_list.html",
        {
            "captured_properties": items,
            "current_status": status,
            "current_operation_type": operation_type,
            "current_property_type": property_type,
            "current_source_id": source_id,
            "current_entry_mode": entry_mode,
            "current_location_query": location_query,
            "current_search_profile_id": search_profile_id,
            "status_choices": CapturedProperty.Status.choices,
            "operation_type_choices": CapturedProperty.OperationType.choices,
            "property_type_choices": CapturedProperty.PropertyType.choices,
            "entry_mode_choices": CapturedProperty.EntryMode.choices,
            "available_sources": available_sources,
            "available_search_profiles": available_search_profiles,
        },
    )


@login_required
def capturedproperty_detail(request, pk):
    obj = get_object_or_404(
        CapturedProperty.objects.select_related("source", "search_profile"),
        pk=pk,
        owner=request.user,
    )

    try:
        existing_opportunity = obj.opportunity
    except PropertyOpportunity.DoesNotExist:
        existing_opportunity = None

    next_url = request.GET.get("next") or request.session.get("capturedproperty_last_list_url") or "/app/captacion/"

    return render(
        request,
        "inmuebles/capturedproperty_detail.html",
        {
            "item": obj,
            "existing_opportunity": existing_opportunity,
            "next_url": next_url,
        },
    )


@login_required
@require_POST
def capturedproperty_mark_interesting(request, pk):
    obj = get_object_or_404(CapturedProperty, pk=pk, owner=request.user)
    obj.is_interesting = True
    obj.last_reviewed_at = timezone.now()
    obj.save(update_fields=["is_interesting", "last_reviewed_at", "updated_at"])
    messages.success(request, "Inmueble marcado como interesante.")
    return redirect("capturedproperty_detail", pk=obj.pk)


@login_required
@require_POST
def capturedproperty_mark_in_review(request, pk):
    obj = get_object_or_404(CapturedProperty, pk=pk, owner=request.user)
    obj.status = CapturedProperty.Status.IN_REVIEW
    obj.review_status = CapturedProperty.ReviewStatus.REVIEWED
    obj.last_reviewed_at = timezone.now()
    obj.save(update_fields=["status", "review_status", "last_reviewed_at", "updated_at"])
    messages.success(request, "Captación marcada en revisión.")
    return redirect(
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or f"/app/captacion/{obj.pk}/"
    )


@login_required
@require_POST
def capturedproperty_convert_to_opportunity(request, pk):
    obj = get_object_or_404(CapturedProperty, pk=pk, owner=request.user)

    now = timezone.now()

    opportunity, created = PropertyOpportunity.objects.get_or_create(
        captured_property=obj,
        defaults={
            "owner": request.user,
            "search_profile": obj.search_profile,
            "title": obj.title,
            "asking_price_current": obj.price,
            "status": PropertyOpportunity.Status.NEW,
            "priority": PropertyOpportunity.Priority.MEDIUM,
            "province": obj.province or "",
            "municipality": obj.municipality or "",
            "summary": obj.description_raw or "",
            "next_action_type": PropertyOpportunity.NextActionType.OTHER,
            "next_action_notes": "Completar contacto, documentación y análisis inicial",
            "next_review_at": now,
            "last_activity_at": now,
        },
    )

    if not created:
        update_fields = []

        if opportunity.owner_id is None:
            opportunity.owner = request.user
            update_fields.append("owner")

        if opportunity.search_profile_id is None and obj.search_profile_id:
            opportunity.search_profile = obj.search_profile
            update_fields.append("search_profile")

        if update_fields:
            update_fields.append("updated_at")
            opportunity.save(update_fields=update_fields)

    if created:
        OpportunityActivity.objects.create(
            opportunity=opportunity,
            activity_type=OpportunityActivity.ActivityType.CREATED,
            summary="Oportunidad creada desde captación",
            details=(
                f"Conversión inicial desde CapturedProperty #{obj.pk}. "
                f"Fuente: {obj.source} | URL: {obj.source_url or 'sin url'}"
            ),
            created_by=request.user,
            extra_data={
                "captured_property_id": obj.pk,
                "source_url": obj.source_url or "",
                "captured_status": obj.status,
                "review_status": obj.review_status,
            },
        )

    obj.status = CapturedProperty.Status.VALIDATED
    obj.review_status = CapturedProperty.ReviewStatus.REVIEWED
    obj.is_interesting = True
    obj.last_reviewed_at = now
    obj.save(update_fields=["status", "review_status", "is_interesting", "last_reviewed_at", "updated_at"])

    if created:
        messages.success(request, "Oportunidad creada correctamente.")
    else:
        messages.info(request, "La oportunidad ya existía.")

    return redirect(request.META.get("HTTP_REFERER", f"/app/oportunidades/{opportunity.pk}/"))


@login_required
@require_POST
def capturedproperty_delete(request, pk):
    obj = get_object_or_404(CapturedProperty, pk=pk, owner=request.user)

    try:
        existing_opportunity = obj.opportunity
    except PropertyOpportunity.DoesNotExist:
        existing_opportunity = None

    list_url = request.session.get("capturedproperty_last_list_url") or ""
    next_url = request.POST.get("next") or list_url or request.META.get("HTTP_REFERER") or ""

    if existing_opportunity:
        messages.error(
            request,
            "No se puede eliminar esta captación porque ya tiene una oportunidad asociada.",
        )
        return redirect(next_url or "/app/captacion/")

    deleted_detail_url = f"/app/captacion/{obj.pk}/"

    # Nunca volver al detalle de una captación eliminada.
    # Primero intentamos volver a la última bandeja exacta, con todos sus filtros.
    if not next_url or deleted_detail_url in next_url:
        next_url = list_url or "/app/captacion/"

    obj.delete()
    messages.success(request, "Captación eliminada correctamente.")
    return redirect(next_url)


@login_required
def capturedproperty_manual_create(request):
    if request.method == "POST":
        form = CapturedPropertyManualForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Captación manual creada: "{obj.title}".')
            return redirect("/app/captacion/")
    else:
        form = CapturedPropertyManualForm(user=request.user)

    return render(
        request,
        "inmuebles/capturedproperty_manual_form.html",
        {
            "form": form,
        },
    )


@login_required
def capturedproperty_edit(request, pk):
    obj = get_object_or_404(CapturedProperty, pk=pk, owner=request.user)

    if request.method == "POST":
        form = CapturedPropertyManualForm(request.POST, instance=obj, user=request.user)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.owner = request.user
            updated.save()
            messages.success(request, f'Captación actualizada: "{obj.title}".')
            return redirect(f"/app/captacion/{obj.pk}/")
    else:
        form = CapturedPropertyManualForm(instance=obj, user=request.user)

    return render(
        request,
        "inmuebles/capturedproperty_manual_form.html",
        {
            "form": form,
            "item": obj,
        },
    )