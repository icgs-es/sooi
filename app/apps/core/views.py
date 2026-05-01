from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from apps.busquedas.models import SearchProfile
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import Alert, FollowUpTask, PropertyOpportunity

from .forms import SystemSettingsForm, InternalUserCreateForm, InternalUserUpdateForm
from .models import SystemSettings


def home(request):
    return render(request, "core/home.html")


def privacy_policy(request):
    return render(request, "core/privacy_policy.html")


def terms_of_use(request):
    return render(request, "core/terms_of_use.html")


@login_required
def dashboard(request):
    stats = {
        "search_profiles": SearchProfile.objects.filter(owner=request.user).count(),
        "captured_properties": CapturedProperty.objects.filter(owner=request.user).count(),
        "captured_pending": CapturedProperty.objects.filter(
            owner=request.user,
            status="captured",
        ).count(),
        "opportunities": PropertyOpportunity.objects.filter(owner=request.user).count(),
        "opportunities_active": PropertyOpportunity.objects.filter(
            owner=request.user,
            status__in=["new", "active", "analysis", "negotiation"],
        ).count(),
        "tasks_open": FollowUpTask.objects.filter(
            owner=request.user,
            status__in=["open", "in_progress"],
        ).count(),
        "alerts_new": Alert.objects.filter(
            owner=request.user,
            status="new",
        ).count(),
    }

    recent_captured = (
        CapturedProperty.objects.select_related("source")
        .filter(owner=request.user)
        .order_by("-captured_at")[:5]
    )

    recent_opportunities = (
        PropertyOpportunity.objects.select_related("captured_property")
        .filter(owner=request.user)
        .order_by("-created_at")[:5]
    )

    recent_tasks = (
        FollowUpTask.objects.select_related("property_opportunity", "captured_property")
        .filter(owner=request.user)
        .order_by("-created_at")[:5]
    )

    recent_alerts = (
        Alert.objects.select_related("property_opportunity", "captured_property")
        .filter(owner=request.user)
        .order_by("-created_at")[:5]
    )

    return render(
        request,
        "core/dashboard.html",
        {
            "stats": stats,
            "recent_captured": recent_captured,
            "recent_opportunities": recent_opportunities,
            "recent_tasks": recent_tasks,
            "recent_alerts": recent_alerts,
        },
    )

@login_required
def system_settings_edit(request):
    settings_obj = SystemSettings.get_solo()

    if request.method == "POST":
        form = SystemSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Configuración guardada correctamente.")
            return redirect("system_settings")
    else:
        form = SystemSettingsForm(instance=settings_obj)

    return render(
        request,
        "core/settings_form.html",
        {
            "form": form,
            "item": settings_obj,
            "section_title": "Configuración",
        },
    )
    
User = get_user_model()


@login_required
def internal_user_list(request):
    if request.user.is_superuser:
        items = User.objects.order_by("username")
    else:
        items = User.objects.filter(pk=request.user.pk)

    return render(
        request,
        "core/user_list.html",
        {
            "items": items,
            "can_manage_users": request.user.is_superuser,
        },
    )


@login_required
def internal_user_create(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("No autorizado.")

    if request.method == "POST":
        form = InternalUserCreateForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'Usuario creado: "{obj.username}".')
            return redirect("internal_user_list")
    else:
        form = InternalUserCreateForm()

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "item": None,
        },
    )
    
@login_required
def internal_user_edit(request, pk):
    item = get_object_or_404(User, pk=pk)

    if not request.user.is_superuser and request.user.pk != item.pk:
        return HttpResponseForbidden("No autorizado.")

    if item.is_superuser and not request.user.is_superuser and request.user.pk != item.pk:
        return HttpResponseForbidden("No puedes editar este superusuario.")

    if request.method == "POST":
        form = InternalUserUpdateForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'Usuario actualizado: "{item.username}".')
            return redirect("internal_user_list")
    else:
        form = InternalUserUpdateForm(instance=item)

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "item": item,
        },
    )