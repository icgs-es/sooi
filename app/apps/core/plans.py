from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

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

PLAN_CHOICES = tuple((code, values["name"]) for code, values in SOOI_PLANS.items())

PLAN_GROUPS = {
    "sooi_plan_starter": "starter",
    "sooi_plan_professional": "professional",
    "sooi_plan_business": "business",
}


@dataclass(frozen=True)
class Entitlement:
    trial_active: bool
    trial_expired: bool
    plan_code: str
    capabilities: frozenset[str]
    limits: MappingProxyType
    unlimited_active_searches: bool = False
    unlimited_ai_credits: bool = False

    @property
    def plan(self):
        return SOOI_PLANS[self.plan_code]


def resolve_entitlement(user, *, now=None):
    """Resolve the complete server-side commercial contract, fail-closed."""
    if not user or not getattr(user, "is_authenticated", False):
        code = "starter"
        profile = None
    else:
        try:
            profile = user.profile
        except (AttributeError, ObjectDoesNotExist):
            profile = None

        if getattr(user, "is_superuser", False):
            code = "business"
        elif profile is None:
            code = "starter"
        elif profile.is_trial:
            current = now or timezone.now()
            active = bool(profile.trial_start and profile.trial_end and profile.trial_start <= current < profile.trial_end)
            code = "professional" if active else "starter"
        else:
            code = profile.plan if profile.plan in SOOI_PLANS else "starter"

    trial_active = bool(profile and profile.is_trial and code == "professional")
    trial_expired = bool(profile and profile.is_trial and not trial_active)
    plan = SOOI_PLANS[code]
    limits = MappingProxyType({
        "max_active_searches": int(plan["max_active_searches"]),
        "monthly_ai_credits": int(plan["monthly_ai_credits"]),
    })
    capabilities = frozenset({"searches", "ai_discovery"})
    return Entitlement(
        trial_active=trial_active,
        trial_expired=trial_expired,
        plan_code=code,
        capabilities=capabilities,
        limits=limits,
        unlimited_active_searches=bool(getattr(user, "is_superuser", False)),
        unlimited_ai_credits=bool(
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ),
    )


def get_user_plan_code(user):
    return resolve_entitlement(user).plan_code


def get_user_plan(user):
    return resolve_entitlement(user).plan


def get_max_active_searches(user):
    entitlement = resolve_entitlement(user)
    return 999999 if entitlement.unlimited_active_searches else entitlement.limits["max_active_searches"]
