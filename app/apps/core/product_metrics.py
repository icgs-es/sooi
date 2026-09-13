"""Privacy-bounded daily product metrics for WP-09.

Queries select only counts and closed categorical dimensions.  They never select
identity, free text, URLs, property data, or notification errors.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db.models import Case, CharField, Count, Exists, F, OuterRef, Q, Value, When
from django.utils import timezone

from apps.busquedas.models import SearchProfile
from apps.core.models import DailyProductMetric, DemoRequest, UserProfile
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import FollowUpTask, PropertyOpportunity

CANONICAL_TIME_ZONE = "Europe/Madrid"
SUPPRESSION_THRESHOLD = 10
CALCULATION_VERSION = 1
ALLOWED_METRICS = (
    "registration_completed", "first_search_created", "first_capture_available",
    "first_opportunity_created", "first_dated_next_action",
    "canonical_activation_completed", "trial_expired", "demo_request_pending",
    "demo_request_notified", "demo_request_delivery_failed",
)
ALLOWED_DIMENSIONS = ("all", "signup_source", "profile_type")
ALLOWED_DIMENSIONS_BY_METRIC = {
    "registration_completed": ("all", "signup_source"),
    "first_search_created": ("all",),
    "first_capture_available": ("all",),
    "first_opportunity_created": ("all",),
    "first_dated_next_action": ("all",),
    "canonical_activation_completed": ("all", "signup_source"),
    "trial_expired": ("all", "signup_source"),
    "demo_request_pending": ("all", "profile_type"),
    "demo_request_notified": ("all", "profile_type"),
    "demo_request_delivery_failed": ("all", "profile_type"),
}
SIGNUP_SOURCE_VALUES = ("self_service", "unknown")
PROFILE_TYPE_VALUES = tuple(value for value, _label in DemoRequest.ProfileType.choices) + ("unknown",)
SNAPSHOT_METRICS = ("demo_request_pending", "demo_request_delivery_failed")


def _bounds(day):
    zone = ZoneInfo(CANONICAL_TIME_ZONE)
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def last_closed_day():
    return timezone.now().astimezone(ZoneInfo(CANONICAL_TIME_ZONE)).date() - timedelta(days=1)


def validate_closed_day(day):
    if day > last_closed_day():
        raise ValueError("only closed natural days may be calculated")


def validate_snapshot_day(metric_name, day):
    if metric_name in SNAPSHOT_METRICS and day != last_closed_day():
        raise ValueError("snapshot metrics only support the latest closed natural day")


def _normalised(field, allowed):
    return Case(
        When(**{f"{field}__in": allowed}, then=F(field)),
        default=Value("unknown"), output_field=CharField(),
    )


def _rows(metric, total, dimension=None, groups=(), allowed_values=()):
    rows = [(metric, "all", "all", total)]
    if dimension:
        counts = {item["dimension_value"]: item["value"] for item in groups}
        rows.extend((metric, dimension, value, counts.get(value, 0)) for value in allowed_values)
    return rows


def _first_exists(model, owner_field, date_field, start, end, extra=Q()):
    base = model.objects.filter(extra, **{owner_field: OuterRef("pk")})
    during = base.filter(**{f"{date_field}__gte": start, f"{date_field}__lt": end})
    before = base.filter(**{f"{date_field}__lt": start})
    return Exists(during), Exists(before)


def _qualified_users(day, milestone):
    start, end = _bounds(day)
    User = get_user_model()
    users = User.objects.filter(is_staff=False, is_superuser=False)
    if milestone == "search":
        during, before = _first_exists(SearchProfile, "owner_id", "created_at", start, end)
    elif milestone == "capture":
        valid = Q(search_profile__isnull=True) | Q(search_profile__owner_id=F("owner_id"))
        during, before = _first_exists(CapturedProperty, "owner_id", "captured_at", start, end, valid)
    elif milestone == "opportunity":
        valid = Q(captured_property__owner_id=F("owner_id"))
        during, before = _first_exists(PropertyOpportunity, "owner_id", "created_at", start, end, valid)
    else:
        task_valid = (
            (Q(property_opportunity__isnull=True) | Q(property_opportunity__owner_id=F("owner_id")))
            & (Q(captured_property__isnull=True) | Q(captured_property__owner_id=F("owner_id")))
            & ~Q(task_type=FollowUpTask.TaskType.REVIEW, property_opportunity__isnull=False)
        )
        task_during, task_before = _first_exists(FollowUpTask, "owner_id", "due_date", start, end, task_valid)
        opp_valid = Q(captured_property__owner_id=F("owner_id"), next_review_at__isnull=False)
        opp_during, opp_before = _first_exists(PropertyOpportunity, "owner_id", "next_review_at", start, end, opp_valid)
        return users.annotate(td=task_during, tb=task_before, od=opp_during, ob=opp_before).filter(
            Q(td=True) | Q(od=True), tb=False, ob=False,
        )
    return users.annotate(during=during, before=before).filter(during=True, before=False)


def _signup_rows(metric, queryset):
    groups = queryset.annotate(
        dimension_value=_normalised("profile__signup_source", SIGNUP_SOURCE_VALUES),
    ).values("dimension_value").annotate(value=Count("pk", distinct=True)).order_by()
    return _rows(metric, queryset.count(), "signup_source", groups, SIGNUP_SOURCE_VALUES)


def _activation_users(day):
    start, end = _bounds(day)
    User = get_user_model()
    users = User.objects.filter(is_staff=False, is_superuser=False)
    search_end = Exists(SearchProfile.objects.filter(owner_id=OuterRef("pk"), created_at__lt=end))
    search_start = Exists(SearchProfile.objects.filter(owner_id=OuterRef("pk"), created_at__lt=start))
    capture_valid = Q(search_profile__isnull=True) | Q(search_profile__owner_id=F("owner_id"))
    capture_end = Exists(CapturedProperty.objects.filter(capture_valid, owner_id=OuterRef("pk"), captured_at__lt=end))
    capture_start = Exists(CapturedProperty.objects.filter(capture_valid, owner_id=OuterRef("pk"), captured_at__lt=start))
    opp_valid = Q(captured_property__owner_id=F("owner_id"))
    opp_end = Exists(PropertyOpportunity.objects.filter(opp_valid, owner_id=OuterRef("pk"), created_at__lt=end))
    opp_start = Exists(PropertyOpportunity.objects.filter(opp_valid, owner_id=OuterRef("pk"), created_at__lt=start))
    task_valid = (
        (Q(property_opportunity__isnull=True) | Q(property_opportunity__owner_id=F("owner_id")))
        & (Q(captured_property__isnull=True) | Q(captured_property__owner_id=F("owner_id")))
        & ~Q(task_type=FollowUpTask.TaskType.REVIEW, property_opportunity__isnull=False)
    )
    action_end = Exists(FollowUpTask.objects.filter(task_valid, owner_id=OuterRef("pk"), due_date__lt=end))
    action_start = Exists(FollowUpTask.objects.filter(task_valid, owner_id=OuterRef("pk"), due_date__lt=start))
    review_end = Exists(PropertyOpportunity.objects.filter(opp_valid, owner_id=OuterRef("pk"), next_review_at__lt=end))
    review_start = Exists(PropertyOpportunity.objects.filter(opp_valid, owner_id=OuterRef("pk"), next_review_at__lt=start))
    return users.annotate(se=search_end, ss=search_start, ce=capture_end, cs=capture_start,
                          oe=opp_end, os=opp_start, ae=action_end, ass=action_start,
                          re=review_end, rs=review_start).filter(
        se=True, ce=True, oe=True,
    ).filter(Q(ae=True) | Q(re=True)).exclude(
        Q(ss=True, cs=True, os=True) & (Q(ass=True) | Q(rs=True))
    )


def calculate_metric(day, metric_name):
    if metric_name not in ALLOWED_METRICS:
        raise ValueError("unknown metric name")
    validate_closed_day(day)
    validate_snapshot_day(metric_name, day)
    start, end = _bounds(day)
    User = get_user_model()
    if metric_name == "registration_completed":
        qs = User.objects.filter(date_joined__gte=start, date_joined__lt=end, is_staff=False, is_superuser=False)
        return _signup_rows(metric_name, qs)
    if metric_name.startswith("first_"):
        milestone = {"first_search_created": "search", "first_capture_available": "capture",
                     "first_opportunity_created": "opportunity", "first_dated_next_action": "action"}[metric_name]
        qs = _qualified_users(day, milestone)
        return _rows(metric_name, qs.count())
    if metric_name == "canonical_activation_completed":
        return _signup_rows(metric_name, _activation_users(day))
    if metric_name == "trial_expired":
        qs = UserProfile.objects.filter(is_trial=True, trial_end__gte=start, trial_end__lt=end,
                                        user__is_staff=False, user__is_superuser=False)
        groups = qs.annotate(dimension_value=_normalised("signup_source", SIGNUP_SOURCE_VALUES)).values(
            "dimension_value").annotate(value=Count("pk")).order_by()
        return _rows(metric_name, qs.count(), "signup_source", groups, SIGNUP_SOURCE_VALUES)
    demos = DemoRequest.objects.all()
    if metric_name == "demo_request_pending":
        demos = demos.filter(created_at__lt=end, delivery_status=DemoRequest.DeliveryStatus.PENDING)
    elif metric_name == "demo_request_notified":
        demos = demos.filter(notified_at__gte=start, notified_at__lt=end)
    else:
        demos = demos.filter(created_at__lt=end, delivery_status=DemoRequest.DeliveryStatus.FAILED)
    groups = demos.annotate(dimension_value=_normalised("profile_type", PROFILE_TYPE_VALUES)).values(
        "dimension_value").annotate(value=Count("pk")).order_by()
    return _rows(metric_name, demos.count(), "profile_type", groups, PROFILE_TYPE_VALUES)


def calculate_day(day):
    rows = []
    for metric in ALLOWED_METRICS:
        if metric not in SNAPSHOT_METRICS or day == last_closed_day():
            rows.extend(calculate_metric(day, metric))
    return rows


def published_metrics(day, metric_name, dimension_name="all", calculation_version=CALCULATION_VERSION):
    """Return only safe values; ``suppressed`` never includes the stored count.

    If any dimensional cell is small, the complete breakdown is suppressed so a
    total-minus-cell query cannot reconstruct that cell.
    """
    if metric_name not in ALLOWED_METRICS or dimension_name not in ALLOWED_DIMENSIONS_BY_METRIC[metric_name]:
        raise ValueError("metric and dimension are not in the publication catalogue")
    rows = list(DailyProductMetric.objects.filter(
        natural_day=day, metric_name=metric_name, dimension_name=dimension_name,
        calculation_version=calculation_version, is_complete=True,
    ).values("dimension_value", "value").order_by("dimension_value"))
    suppress_breakdown = dimension_name != "all" and any(row["value"] < SUPPRESSION_THRESHOLD for row in rows)
    return [{"dimension_value": row["dimension_value"],
             "value": "suppressed" if suppress_breakdown or row["value"] < SUPPRESSION_THRESHOLD else row["value"]}
            for row in rows]
