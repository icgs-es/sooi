from django.conf import settings

from .models import UserProfile

SOOI_PLANS = {
    "starter": {
        "code": "starter",
        "name": "Starter",
        "max_active_searches": 2,
        "monthly_ai_credits": 50,
    },
    "professional": {
        "code": "professional",
        "name": "Professional",
        "max_active_searches": 6,
        "monthly_ai_credits": 300,
    },
    "business": {
        "code": "business",
        "name": "Business",
        "max_active_searches": 15,
        "monthly_ai_credits": 1200,
    },
}

PLAN_GROUPS = {
    "sooi_plan_starter": "starter",
    "sooi_plan_professional": "professional",
    "sooi_plan_business": "business",
}


def get_user_plan_code(user):
    if not user or not user.is_authenticated:
        return "starter"

    # Permite configurar excepciones desde settings si más adelante hace falta.
    overrides = getattr(settings, "SOOI_USER_PLAN_OVERRIDES", {})
    username = getattr(user, "username", "")
    email = getattr(user, "email", "")

    if username in overrides:
        return overrides[username]
    if email in overrides:
        return overrides[email]

    # Plan por grupos Django. Sin migraciones y configurable desde admin.
    for group_name, plan_code in PLAN_GROUPS.items():
        if user.groups.filter(name=group_name).exists():
            return plan_code

    return getattr(settings, "SOOI_DEFAULT_PLAN_CODE", "professional")


def get_user_plan(user):
    # Trial expirado → degradar a starter sin importar el grupo
    try:
        if user.profile.is_trial and user.profile.trial_expired:
            return SOOI_PLANS["starter"]
    except (AttributeError, UserProfile.DoesNotExist):
        pass

    code = get_user_plan_code(user)
    return SOOI_PLANS.get(code, SOOI_PLANS["professional"])


def get_max_active_searches(user):
    return int(get_user_plan(user)["max_active_searches"])
