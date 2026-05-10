from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from .models import Alert, FollowUpTask, OpportunityActivity, PropertyOpportunity, BrokerCompany, OpportunityContact

from django.utils import timezone
from apps.busquedas.models import SearchProfile
from .forms import AlertForm, OpportunityActivityQuickForm, OpportunityForm, BrokerCompanyForm, OpportunityContactForm


@login_required
def agenda_view(request):
    now = timezone.now()
    window_end = now + timedelta(days=30)
    calendar_end = now + timedelta(days=13)

    base_tasks = (
        FollowUpTask.objects
        .select_related(
            "property_opportunity",
            "property_opportunity__search_profile",
            "captured_property",
            "captured_property__search_profile",
        )
        .filter(owner=request.user, due_date__isnull=False)
        .exclude(status__in=[
            FollowUpTask.Status.DONE,
            FollowUpTask.Status.CANCELLED,
        ])
        .filter(due_date__lte=window_end)
        .order_by("due_date")
    )

    review_task_opportunity_ids = list(
        base_tasks.filter(
            task_type=FollowUpTask.TaskType.REVIEW,
            property_opportunity_id__isnull=False,
        ).values_list("property_opportunity_id", flat=True)
    )

    base_opportunities = (
        PropertyOpportunity.objects
        .select_related("captured_property", "search_profile")
        .filter(owner=request.user, next_review_at__isnull=False)
        .exclude(status__in=[
            PropertyOpportunity.Status.DISCARDED,
            PropertyOpportunity.Status.CLOSED,
        ])
        .exclude(id__in=review_task_opportunity_ids)
        .filter(next_review_at__lte=window_end)
        .order_by("next_review_at")
    )

    calendar_days = []
    for offset in range(14):
        day = (timezone.localtime(now) + timedelta(days=offset)).date()
        calendar_days.append({
            "date": day,
            "tasks": [],
            "opportunities": [],
        })

    calendar_index = {item["date"]: item for item in calendar_days}

    for task in base_tasks.filter(
        due_date__date__gte=timezone.localtime(now).date(),
        due_date__date__lte=timezone.localtime(calendar_end).date(),
    ):
        day = timezone.localtime(task.due_date).date()
        if day in calendar_index:
            calendar_index[day]["tasks"].append(task)

    for opportunity in base_opportunities.filter(
        next_review_at__date__gte=timezone.localtime(now).date(),
        next_review_at__date__lte=timezone.localtime(calendar_end).date(),
    ):
        day = timezone.localtime(opportunity.next_review_at).date()
        if day in calendar_index:
            calendar_index[day]["opportunities"].append(opportunity)

    return render(
        request,
        "seguimiento/agenda.html",
        {
            "now": now,
            "window_end": window_end,
            "calendar_days": calendar_days,
            "overdue_tasks": base_tasks.filter(due_date__lt=now),
            "upcoming_tasks": base_tasks.filter(due_date__gte=now),
            "overdue_opportunities": base_opportunities.filter(next_review_at__lt=now),
            "upcoming_opportunities": base_opportunities.filter(next_review_at__gte=now),
        },
    )


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
def opportunity_complete_review(request, pk):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("agenda_view")

    previous_review_at = item.next_review_at

    if previous_review_at:
        previous_text = timezone.localtime(previous_review_at).strftime("%d/%m/%Y %H:%M")
    else:
        previous_text = "sin fecha previa"

    activity = OpportunityActivity.objects.create(
        opportunity=item,
        activity_type=OpportunityActivity.ActivityType.NOTE,
        summary="Revisión marcada como realizada",
        details=f"Revisión operativa completada. Fecha prevista: {previous_text}.",
        created_by=request.user,
    )

    FollowUpTask.objects.filter(
        owner=request.user,
        property_opportunity=item,
        task_type=FollowUpTask.TaskType.REVIEW,
        status__in=[
            FollowUpTask.Status.OPEN,
            FollowUpTask.Status.IN_PROGRESS,
        ],
    ).update(
        status=FollowUpTask.Status.DONE,
        updated_at=timezone.now(),
    )

    item.next_review_at = None
    item.last_activity_at = activity.created_at
    item.save(update_fields=["next_review_at", "last_activity_at", "updated_at"])

    messages.success(request, "Revisión marcada como realizada.")
    return redirect("agenda_view")


@login_required
def opportunity_schedule_review(request, pk, days):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("opportunity_detail", pk=item.pk)

    allowed_days = {1, 3, 7, 14}
    if days not in allowed_days:
        messages.error(request, "Plazo de revisión no válido.")
        return redirect("opportunity_detail", pk=item.pk)

    review_at = timezone.now() + timedelta(days=days)
    item.next_review_at = review_at
    item.last_activity_at = timezone.now()
    item.save(update_fields=["next_review_at", "last_activity_at", "updated_at"])

    OpportunityActivity.objects.create(
        opportunity=item,
        activity_type=OpportunityActivity.ActivityType.NOTE,
        summary=f"Revisión programada en {days} día(s)",
        details=f"Próxima revisión programada para el {review_at.strftime('%d/%m/%Y %H:%M')}.",
        created_by=request.user,
    )

    messages.success(request, "Revisión programada correctamente.")
    return redirect("opportunity_detail", pk=item.pk)


@login_required
def opportunity_quick_action(request, pk, action_type):
    item = get_object_or_404(PropertyOpportunity, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("opportunity_detail", pk=item.pk)

    action_map = {
        "call": {
            "activity_type": OpportunityActivity.ActivityType.CALL,
            "summary": "Llamada registrada",
            "details": "Acción rápida: llamada vinculada a la oportunidad.",
        },
        "whatsapp": {
            "activity_type": OpportunityActivity.ActivityType.WHATSAPP,
            "summary": "WhatsApp registrado",
            "details": "Acción rápida: contacto por WhatsApp vinculado a la oportunidad.",
        },
        "email": {
            "activity_type": OpportunityActivity.ActivityType.EMAIL,
            "summary": "Email registrado",
            "details": "Acción rápida: email vinculado a la oportunidad.",
        },
        "visit": {
            "activity_type": OpportunityActivity.ActivityType.VISIT,
            "summary": "Visita registrada",
            "details": "Acción rápida: visita vinculada a la oportunidad.",
        },
        "review": {
            "activity_type": OpportunityActivity.ActivityType.NOTE,
            "summary": "Revisión operativa registrada",
            "details": "Acción rápida: revisión de la oportunidad.",
        },
        "docs": {
            "activity_type": OpportunityActivity.ActivityType.DOCUMENT_REQUEST,
            "summary": "Solicitud de documentación registrada",
            "details": "Acción rápida: documentación solicitada para la oportunidad.",
        },
    }

    config = action_map.get(action_type)
    if config is None:
        messages.error(request, "Acción rápida no válida.")
        return redirect("opportunity_detail", pk=item.pk)

    activity = OpportunityActivity.objects.create(
        opportunity=item,
        activity_type=config["activity_type"],
        summary=config["summary"],
        details=config["details"],
        created_by=request.user,
    )

    item.last_activity_at = activity.created_at
    item.save(update_fields=["last_activity_at", "updated_at"])

    messages.success(request, f"Actividad registrada: {config['summary']}")
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

    if status == "all":
        pass
    elif status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status__in=[
            FollowUpTask.Status.OPEN,
            FollowUpTask.Status.IN_PROGRESS,
        ])

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
def task_mark_done(request, pk):
    item = get_object_or_404(FollowUpTask, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("agenda_view")

    if item.status != FollowUpTask.Status.DONE:
        item.status = FollowUpTask.Status.DONE
        item.save(update_fields=["status", "updated_at"])

        if item.property_opportunity:
            activity = OpportunityActivity.objects.create(
                opportunity=item.property_opportunity,
                activity_type=OpportunityActivity.ActivityType.TASK,
                summary="Tarea marcada como hecha",
                details=f"Tarea completada desde agenda: {item.title}",
                created_by=request.user,
            )

            opportunity = item.property_opportunity
            update_fields = ["last_activity_at", "updated_at"]
            opportunity.last_activity_at = activity.created_at

            if item.task_type == FollowUpTask.TaskType.REVIEW:
                FollowUpTask.objects.filter(
                    owner=request.user,
                    property_opportunity=opportunity,
                    task_type=FollowUpTask.TaskType.REVIEW,
                    status__in=[
                        FollowUpTask.Status.OPEN,
                        FollowUpTask.Status.IN_PROGRESS,
                    ],
                ).exclude(pk=item.pk).update(
                    status=FollowUpTask.Status.DONE,
                    updated_at=timezone.now(),
                )

                opportunity.next_review_at = None
                update_fields.append("next_review_at")

            opportunity.save(update_fields=update_fields)

    messages.success(request, "Tarea marcada como hecha.")
    return redirect("agenda_view")


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