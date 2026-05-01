from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from .models import Alert, FollowUpTask, OpportunityActivity, PropertyOpportunity, BrokerCompany, OpportunityContact

from django.utils import timezone
from apps.busquedas.models import SearchProfile
from .forms import AlertForm, OpportunityActivityQuickForm, OpportunityForm, BrokerCompanyForm, OpportunityContactForm


@login_required
def opportunity_list(request):
    status = request.GET.get("status", "").strip()
    priority = request.GET.get("priority", "").strip()
    search_profile_id = request.GET.get("search_profile_id", "").strip()

    qs = (
        PropertyOpportunity.objects
        .select_related("captured_property", "broker_company", "main_contact", "search_profile")
        .filter(owner=request.user)
        .order_by("-created_at")
    )

    if status:
        qs = qs.filter(status=status)

    if priority:
        qs = qs.filter(priority=priority)

    if search_profile_id:
        qs = qs.filter(search_profile_id=search_profile_id)

    available_search_profiles = SearchProfile.objects.filter(owner=request.user).order_by("status", "name")

    return render(
        request,
        "seguimiento/opportunity_list.html",
        {
            "opportunities": qs,
            "current_status": status,
            "current_priority": priority,
            "current_search_profile_id": search_profile_id,
            "status_choices": PropertyOpportunity.Status.choices,
            "priority_choices": PropertyOpportunity.Priority.choices,
            "available_search_profiles": available_search_profiles,
        },
    )

@login_required
def opportunity_detail(request, pk):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)
    related_tasks = item.tasks.order_by("status", "due_date", "-created_at")
    related_alerts = item.alerts.order_by("-created_at")
    related_activities = item.activities.order_by("-created_at")
    activity_form = OpportunityActivityQuickForm()

    return render(
        request,
        "seguimiento/opportunity_detail.html",
        {
            "item": item,
            "related_tasks": related_tasks,
            "related_alerts": related_alerts,
            "related_activities": related_activities,
            "activity_form": activity_form,
        },
    )

@login_required
def opportunity_add_activity(request, pk):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("opportunity_detail", pk=item.pk)

    form = OpportunityActivityQuickForm(request.POST)
    if form.is_valid():
        activity = form.save(commit=False)
        activity.opportunity = item
        activity.created_by = request.user
        activity.save()

        item.last_activity_at = activity.created_at
        item.save(update_fields=["last_activity_at", "updated_at"])

    return redirect("opportunity_detail", pk=item.pk)

@login_required
def opportunity_edit(request, pk):
    item = get_object_or_404(
        PropertyOpportunity.objects.select_related(
            "captured_property",
            "broker_company",
            "main_contact",
            "assigned_to",
        ),
        pk=pk,
        owner=request.user,
    )

    if request.method == "POST":
        form = OpportunityForm(request.POST, instance=item, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.last_activity_at = timezone.now()
            obj.save()

            OpportunityActivity.objects.create(
                opportunity=obj,
                activity_type=OpportunityActivity.ActivityType.NOTE,
                summary="Oportunidad actualizada",
                details="Actualización manual desde la ficha interna de SOOI.",
                created_by=request.user,
            )

            messages.success(request, "Oportunidad actualizada correctamente.")
            return redirect("opportunity_detail", pk=obj.pk)
    else:
        form = OpportunityForm(instance=item, user=request.user)

    return render(
        request,
        "seguimiento/opportunity_form.html",
        {
            "form": form,
            "item": item,
            "section_title": "Editar oportunidad",
        },
    )
 
@login_required
def opportunity_delete(request, pk):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)

    if request.method == "POST":
        title = item.title
        item.delete()
        messages.success(request, f'Oportunidad eliminada: "{title}".')
        return redirect("opportunity_list")

    return render(
        request,
        "seguimiento/opportunity_confirm_delete.html",
        {
            "item": item,
        },
    )
    
@login_required
def task_list(request):
    status = request.GET.get("status", "").strip()
    priority = request.GET.get("priority", "").strip()
    task_search_profile_id = request.GET.get("search_profile_id", "").strip()
    search_profile_id = request.GET.get("search_profile_id", "").strip()

    qs = (
        FollowUpTask.objects
        .select_related("property_opportunity", "property_opportunity__search_profile", "captured_property", "captured_property__search_profile", "assigned_to")
        .filter(owner=request.user)
        .order_by("-created_at")
    )

    if status:
        qs = qs.filter(status=status)

    if priority:
        qs = qs.filter(priority=priority)

    if search_profile_id:
        qs = qs.filter(search_profile_id=search_profile_id)

    available_search_profiles = SearchProfile.objects.filter(owner=request.user).order_by("status", "name")

    return render(
        request,
        "seguimiento/task_list.html",
        {
            "tasks": qs,
            "current_status": status,
            "current_priority": priority,
            "current_search_profile_id": search_profile_id,
            "status_choices": FollowUpTask.Status.choices,
            "priority_choices": FollowUpTask.Priority.choices,
        },
    )


@login_required
def task_detail(request, pk):
    item = get_object_or_404(
        FollowUpTask.objects.select_related(
            "property_opportunity",
            "captured_property",
            "assigned_to",
        ),
        pk=pk,
        owner=request.user,
    )

    return render(
        request,
        "seguimiento/task_detail.html",
        {
            "item": item,
        },
    )

@login_required
def task_delete(request, pk):
    item = get_object_or_404(FollowUpTask, pk=pk, owner=request.user)

    if request.method == "POST":
        title = item.title
        item.delete()
        messages.success(request, f'Tarea eliminada: "{title}".')
        return redirect("task_list")

    return render(
        request,
        "seguimiento/task_confirm_delete.html",
        {
            "item": item,
        },
    )
    
@login_required
def alert_list(request):
    status = request.GET.get("status", "").strip()
    severity = request.GET.get("severity", "").strip()

    qs = (
        Alert.objects
        .select_related("property_opportunity", "captured_property")
        .filter(owner=request.user)
        .order_by("-created_at")
    )

    if status:
        qs = qs.filter(status=status)

    if severity:
        qs = qs.filter(severity=severity)

    return render(
        request,
        "seguimiento/alert_list.html",
        {
            "alerts": qs,
            "current_status": status,
            "current_severity": severity,
            "status_choices": Alert.Status.choices,
            "severity_choices": Alert.Severity.choices,
        },
    )

@login_required
def alert_detail(request, pk):
    item = get_object_or_404(
        Alert.objects.select_related(
            "property_opportunity",
            "captured_property",
        ),
        pk=pk,
        owner=request.user,
    )

    return render(
        request,
        "seguimiento/alert_detail.html",
        {
            "item": item,
        },
    )
    
@login_required
def alert_delete(request, pk):
    item = get_object_or_404(Alert, pk=pk, owner=request.user)

    if request.method == "POST":
        title = item.title
        item.delete()
        messages.success(request, f'Alerta eliminada: "{title}".')
        return redirect("alert_list")

    return render(
        request,
        "seguimiento/alert_confirm_delete.html",
        {
            "item": item,
        },
    )
    
@login_required
def broker_company_list_create(request):
    items = BrokerCompany.objects.filter(owner=request.user).order_by("name")

    if request.method == "POST":
        form = BrokerCompanyForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Comercializadora creada: "{obj.name}".')
            return redirect("broker_company_list")
    else:
        form = BrokerCompanyForm()

    return render(
        request,
        "seguimiento/broker_company_list.html",
        {
            "items": items,
            "form": form,
        },
    )


@login_required
def opportunity_contact_list_create(request):
    items = OpportunityContact.objects.filter(owner=request.user).order_by("full_name")

    if request.method == "POST":
        form = OpportunityContactForm(request.POST)
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.owner = request.user
                obj.save()
                messages.success(request, f'Contacto creado: "{obj.full_name}".')
                return redirect("opportunity_contact_list")
            except Exception:
                messages.error(
                    request,
                    "No se pudo crear el contacto. Revisa los datos e inténtalo de nuevo.",
                )
    else:
        form = OpportunityContactForm()

    return render(
        request,
        "seguimiento/opportunity_contact_list.html",
        {
            "items": items,
            "form": form,
        },
    )


@login_required
def alert_list_create(request):
    items = (
        Alert.objects.select_related("property_opportunity")
        .filter(owner=request.user)
        .order_by("-created_at")
    )

    if request.method == "POST":
        form = AlertForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Alerta creada: "{obj.title}".')
            return redirect("alert_list")
    else:
        form = AlertForm(user=request.user)

    return render(
        request,
        "seguimiento/alert_list.html",
        {
            "items": items,
            "form": form,
        },
    )

@login_required
def alert_edit(request, pk):
    item = get_object_or_404(Alert, pk=pk, owner=request.user)

    if request.method == "POST":
        form = AlertForm(request.POST, instance=item, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Alerta actualizada: "{item.title}".')
            return redirect("alert_detail", pk=item.pk)
    else:
        form = AlertForm(instance=item, user=request.user)

    return render(
        request,
        "seguimiento/alert_form.html",
        {
            "item": item,
            "form": form,
        },
    )