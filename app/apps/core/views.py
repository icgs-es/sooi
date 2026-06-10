from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from apps.busquedas.models import SearchProfile
from apps.core.access import is_internal_admin
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import Alert, FollowUpTask, PropertyOpportunity

from .forms import DemoRequestForm, RegistrationForm, SystemSettingsForm, InternalUserCreateForm, InternalUserUpdateForm
from .models import SystemSettings, UserProfile


def home(request):
    return render(request, "core/home.html")


def privacy_policy(request):
    return render(request, "core/privacy_policy.html")


def terms_of_use(request):
    return render(request, "core/terms_of_use.html")


@login_required
def dashboard(request):
    captured_total = CapturedProperty.objects.filter(owner=request.user).count()
    opportunities_total = PropertyOpportunity.objects.filter(owner=request.user).count()

    opportunities_active_qs = PropertyOpportunity.objects.filter(
        owner=request.user,
        status__in=["new", "active", "analysis", "negotiation"],
    )

    stats = {
        "search_profiles": SearchProfile.objects.filter(owner=request.user).count(),
        "search_profiles_active": SearchProfile.objects.filter(
            owner=request.user,
            status__in=["active", "paused"],
        ).count(),

        "captured_properties": captured_total,
        "captured_pending": CapturedProperty.objects.filter(
            owner=request.user,
            status="captured",
        ).count(),
        "captured_review_queue": CapturedProperty.objects.filter(
            owner=request.user,
            review_status="pending",
        ).exclude(status="discarded").count(),
        "captured_interesting": CapturedProperty.objects.filter(
            owner=request.user,
            is_interesting=True,
        ).count(),
        "captured_duplicates": CapturedProperty.objects.filter(
            owner=request.user,
            possible_duplicate=True,
        ).exclude(status="discarded").count(),

        "opportunities": opportunities_total,
        "opportunities_active": opportunities_active_qs.count(),
        "opportunities_high": opportunities_active_qs.filter(priority="high").count(),
        "capture_to_opportunity_ratio": round((opportunities_total / captured_total) * 100) if captured_total else 0,

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

    try:
        profile = request.user.profile
        if profile.is_trial and not profile.trial_expired:
            days_left = (profile.trial_end - timezone.now()).days
            trial_info = {'active': True, 'days_left': days_left}
        elif profile.trial_expired:
            trial_info = {'active': False, 'expired': True}
        else:
            trial_info = None
    except Exception:
        trial_info = None

    return render(
        request,
        "core/dashboard.html",
        {
            "stats": stats,
            "recent_captured": recent_captured,
            "recent_opportunities": recent_opportunities,
            "recent_tasks": recent_tasks,
            "recent_alerts": recent_alerts,
            "trial_info": trial_info,
        },
    )


@login_required
def system_settings_edit(request):
    if not is_internal_admin(request.user):
        messages.warning(
            request,
            "La configuración interna de SOOI está reservada a administración."
        )
        return redirect("/app/")

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

def registro_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                data = form.cleaned_data

                user = User.objects.create_user(
                    username=data["email"].lower(),
                    email=data["email"].lower(),
                    password=data["password1"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )

                now = timezone.now()
                UserProfile.objects.create(
                    user=user,
                    company=data.get("company", ""),
                    trial_start=now,
                    trial_end=now + timedelta(days=14),
                    is_trial=True,
                    signup_source="self_service",
                )

                group, _ = Group.objects.get_or_create(name="sooi_plan_professional")
                user.groups.add(group)

            login(request, user, backend="django.contrib.auth.backends.ModelBackend")

            try:
                _ctx = {
                    'nombre': user.first_name or user.email,
                    'trial_end': user.profile.trial_end.strftime('%d de %B de %Y'),
                    'dashboard_url': 'https://sooi.io/app/',
                }
                _msg = EmailMultiAlternatives(
                    subject='Ya tienes acceso a SOOI',
                    body=render_to_string('core/emails/bienvenida.txt', _ctx),
                    from_email='SOOI <no-reply@sooi.io>',
                    to=[user.email],
                )
                _msg.attach_alternative(render_to_string('core/emails/bienvenida.html', _ctx), 'text/html')
                _msg.send(fail_silently=True)
            except Exception:
                pass

            return redirect("dashboard")
    else:
        form = RegistrationForm()

    return render(request, "core/registro.html", {"form": form})


@login_required
def trial_expirado(request):
    return render(request, "core/trial_expirado.html")


def demo_request(request):
    sent = False

    if request.method == "POST":
        form = DemoRequestForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.source_domain = request.get_host()
            obj.save()

            try:
                send_mail(
                    subject=f"Nueva solicitud de demo SOOI · {obj.name}",
                    message=(
                        f"Nombre: {obj.name}\n"
                        f"Email: {obj.email}\n"
                        f"Teléfono: {obj.phone or '-'}\n"
                        f"Perfil: {obj.get_profile_type_display()}\n"
                        f"Dominio: {obj.source_domain}\n\n"
                        f"Mensaje:\n{obj.message or '-'}\n"
                    ),
                    from_email=None,
                    recipient_list=["info@sooi.io"],
                    fail_silently=False,
                )
            except Exception:
                # La solicitud queda guardada aunque falle el aviso por email.
                pass

            sent = True
            form = DemoRequestForm()
    else:
        form = DemoRequestForm()

    return render(
        request,
        "core/demo_request_form.html",
        {
            "form": form,
            "sent": sent,
        },
    )
