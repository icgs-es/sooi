from django.utils import timezone

from apps.core.plans import get_user_plan


AI_DISCOVERY_CREDITS = 10


def get_current_month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_monthly_ai_credit_limit(user):
    return int(get_user_plan(user).get("monthly_ai_credits", 0))


def get_monthly_ai_credits_used(user, exclude_run_id=None):
    from apps.busquedas.models import SearchRun

    qs = SearchRun.objects.filter(
        search_profile__owner=user,
        execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
        created_at__gte=get_current_month_start(),
    ).exclude(status=SearchRun.Status.FAILED)

    if exclude_run_id:
        qs = qs.exclude(id=exclude_run_id)

    return qs.count() * AI_DISCOVERY_CREDITS


def get_ai_usage_summary(user, exclude_run_id=None):
    plan = get_user_plan(user)
    used = get_monthly_ai_credits_used(user, exclude_run_id=exclude_run_id)

    # Uso interno ICGS / administración:
    # no bloquea exploraciones durante desarrollo, pruebas o demos guiadas.
    # Los usuarios demo/clientes normales siguen sujetos a su plan comercial.
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        internal_limit = 999999
        return {
            "plan_code": "internal",
            "plan_name": "Interno ICGS",
            "monthly_ai_credits": internal_limit,
            "credits_used": used,
            "credits_remaining": internal_limit,
            "ai_discovery_cost": AI_DISCOVERY_CREDITS,
            "is_internal": True,
        }

    limit = get_monthly_ai_credit_limit(user)
    remaining = max(limit - used, 0)

    return {
        "plan_code": plan["code"],
        "plan_name": plan["name"],
        "monthly_ai_credits": limit,
        "credits_used": used,
        "credits_remaining": remaining,
        "ai_discovery_cost": AI_DISCOVERY_CREDITS,
        "is_internal": False,
    }


def can_run_ai_discovery(user, exclude_run_id=None):
    summary = get_ai_usage_summary(user, exclude_run_id=exclude_run_id)
    return summary["credits_remaining"] >= summary["ai_discovery_cost"], summary


def format_ai_quota_message(summary):
    return (
        f"Tu plan {summary['plan_name']} incluye {summary['monthly_ai_credits']} créditos IA al mes. "
        f"Has usado {summary['credits_used']} y esta exploración requiere "
        f"{summary['ai_discovery_cost']} créditos. Mejora tu plan o espera al próximo periodo."
    )
