from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.db import models, transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.busquedas.models import SearchProfile
from apps.core.access import is_internal_admin
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import Alert, FollowUpTask, PropertyOpportunity
from apps.core.observability import emit
from apps.core.daily_workflow import build_daily_workflow

from .forms import DemoRequestForm, RegistrationForm, SystemSettingsForm, InternalUserCreateForm, InternalUserUpdateForm, NotificationPreferenceForm
from .models import NotificationPreference, SystemSettings, UserProfile
from .notifications import retry_demo_notification
from .plans import resolve_entitlement
from .proactive_workflow import render_daily_digest


def home(request):
    return render(request, "core/home.html")


def privacy_policy(request):
    return render(request, "core/privacy_policy.html")


def terms_of_use(request):
    return render(request, "core/terms_of_use.html")


@login_required
def dashboard(request):
    daily_workflow = build_daily_workflow(request.user)

    entitlement = resolve_entitlement(request.user)
    if entitlement.trial_active:
        days_left = max((request.user.profile.trial_end - timezone.now()).days, 0)
        trial_info = {'active': True, 'days_left': days_left}
    elif entitlement.trial_expired:
        trial_info = {'active': False, 'expired': True}
    else:
        trial_info = None

    wp08 = None
    if settings.SOOI_WP08_ENABLED:
        first_search = SearchProfile.objects.filter(owner=request.user).order_by("created_at", "pk").first()
        first_capture = (
            CapturedProperty.objects.filter(
                owner=request.user,
            )
            .filter(
                models.Q(search_profile__isnull=True)
                | models.Q(search_profile__owner=request.user)
            )
            .order_by("captured_at", "pk")
            .first()
        )
        first_opportunity = (
            PropertyOpportunity.objects.filter(
                owner=request.user,
                captured_property__owner=request.user,
            )
            .order_by("created_at", "pk")
            .first()
        )

        active_statuses = [FollowUpTask.Status.OPEN, FollowUpTask.Status.IN_PROGRESS]
        next_task = (
            FollowUpTask.objects.select_related("property_opportunity", "captured_property")
            .filter(owner=request.user, status__in=active_statuses, due_date__isnull=False)
            .filter(
                models.Q(property_opportunity__isnull=True)
                | models.Q(property_opportunity__owner=request.user)
            )
            .filter(
                models.Q(captured_property__isnull=True)
                | models.Q(captured_property__owner=request.user)
            )
            .order_by("due_date", "pk")
            .first()
        )
        next_review = (
            PropertyOpportunity.objects.select_related("captured_property")
            .filter(
                owner=request.user,
                captured_property__owner=request.user,
                status__in=["new", "active", "analysis", "negotiation"],
                next_review_at__isnull=False,
            )
            .order_by("next_review_at", "pk")
            .first()
        )

        # Automatic review tasks mirror an opportunity review. Prefer the task
        # as the single actionable item when both records represent that review.
        if (
            next_task
            and next_review
            and next_task.property_opportunity_id == next_review.pk
            and next_task.task_type == FollowUpTask.TaskType.REVIEW
        ):
            next_review = None

        if next_task and (not next_review or next_task.due_date <= next_review.next_review_at):
            next_action = {
                "label": next_task.title,
                "date": next_task.due_date,
                "url": f"/app/tareas/{next_task.pk}/",
            }
        elif next_review:
            next_action = {
                "label": f"Revisar {next_review.title}",
                "date": next_review.next_review_at,
                "url": f"/app/oportunidades/{next_review.pk}/",
            }
        else:
            next_action = None

        milestones = [
            {"label": "Registro completado", "complete": True},
            {"label": "Primera búsqueda", "complete": first_search is not None},
            {"label": "Primera captación disponible", "complete": first_capture is not None},
            {"label": "Primera oportunidad", "complete": first_opportunity is not None},
            {"label": "Próxima acción fechada", "complete": next_action is not None},
        ]

        if first_search is None:
            primary_cta = {"label": "Crear primera búsqueda", "url": "/app/busquedas/nuevo/"}
        elif first_capture is None:
            primary_cta = {
                "label": "Abrir recorrido de captación",
                "url": f"/app/busquedas/{first_search.pk}/",
            }
        elif first_opportunity is None:
            primary_cta = {
                "label": "Revisar captación",
                "url": f"/app/captacion/{first_capture.pk}/",
            }
        elif next_action is None:
            primary_cta = {
                "label": "Registrar próxima acción",
                "url": f"/app/oportunidades/{first_opportunity.pk}/editar/",
            }
        else:
            primary_cta = {"label": "Abrir próxima acción pendiente", "url": next_action["url"]}

        wp08 = {
            "milestones": milestones,
            "next_milestone": next((item for item in milestones if not item["complete"]), None),
            "primary_cta": primary_cta,
            "next_action": next_action,
            "plan_name": entitlement.plan["name"],
        }

    return render(
        request,
        "core/dashboard.html",
        {
            "daily_workflow": daily_workflow,
            "trial_info": trial_info,
            "wp08": wp08,
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


@login_required
def notification_preferences(request):
    preference = NotificationPreference.objects.filter(owner=request.user).first()

    if request.method == "POST":
        form = NotificationPreferenceForm(
            request.POST,
            instance=preference,
            user=request.user,
        )
        if form.is_valid():
            saved = form.save(commit=False)
            saved.owner = request.user
            saved.save()
            messages.success(request, "Preferencias de notificación actualizadas.")
            return redirect("notification_preferences")
    else:
        form = NotificationPreferenceForm(
            instance=preference,
            initial=None if preference else {
                "daily_digest_enabled": False,
                "daily_digest_time": "08:00",
            },
            user=request.user,
        )

    return render(
        request,
        "core/notification_settings_form.html",
        {"form": form, "preference": preference},
    )


@login_required
@require_GET
def daily_digest_preview(request):
    base_url = request.build_absolute_uri("/").rstrip("/")
    preview = render_daily_digest(request.user, limit=5, base_url=base_url)
    return render(request, "core/daily_digest_preview.html", {"preview": preview})
    
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
                delivered = _msg.send(fail_silently=True)
                emit("smtp_delivery", "success" if delivered else "failure",
                     getattr(request, "correlation_id", None), component="smtp",
                     operation="welcome", owner_id=user.pk,
                     reason_code="accepted" if delivered else "not_accepted")
            except Exception:
                emit("smtp_delivery", "failure", getattr(request, "correlation_id", None),
                     component="smtp", operation="welcome", owner_id=user.pk,
                     reason_code="provider_error")
                pass

            return redirect("dashboard")
    else:
        form = RegistrationForm()

    return render(request, "core/registro.html", {"form": form})


@login_required
def trial_expirado(request):
    return render(request, "core/trial_expirado.html")


def logout_view(request):
    auth_logout(request)
    return redirect('home')


def demo_request(request):
    sent = False

    if request.method == "POST":
        form = DemoRequestForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.source_domain = request.get_host()
            obj.save()

            retry_demo_notification(obj.pk, correlation_id=getattr(request, "correlation_id", None))

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
