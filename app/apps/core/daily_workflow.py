"""Read-only selectors for the action-first daily commercial workflow."""

from datetime import datetime, time, timedelta

from django.db.models import Case, CharField, Count, IntegerField, Q, Value, When
from django.utils import timezone

from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import FollowUpTask, OpportunityActivity, PropertyOpportunity


CAPTURE_REASONS = {
    "ready": "Disponible pendiente de decisión",
    "availability": "Disponibilidad sin verificar",
    "review": "Pendiente de revisión",
    "duplicate": "Posible duplicado",
}
OPPORTUNITY_REASONS = {
    "overdue": "Seguimiento vencido",
    "today": "Próxima revisión hoy",
    "no_next": "Sin próxima revisión programada",
    "upcoming": "Próxima revisión",
}
TASK_REASONS = {
    "overdue": "Tarea vencida",
    "today": "Tarea para hoy",
    "upcoming": "Próxima tarea",
}

ACTIVE_OPPORTUNITY_STATUSES = (
    PropertyOpportunity.Status.NEW,
    PropertyOpportunity.Status.ACTIVE,
    PropertyOpportunity.Status.ANALYSIS,
    PropertyOpportunity.Status.NEGOTIATION,
)
ACTIVE_TASK_STATUSES = (
    FollowUpTask.Status.OPEN,
    FollowUpTask.Status.IN_PROGRESS,
)


def local_day_boundaries(now=None):
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    current_tz = timezone.get_current_timezone()
    start_today = timezone.make_aware(
        datetime.combine(local_now.date(), time.min),
        current_tz,
    )
    start_tomorrow = timezone.make_aware(
        datetime.combine(local_now.date() + timedelta(days=1), time.min),
        current_tz,
    )
    end_upcoming = timezone.make_aware(
        datetime.combine(local_now.date() + timedelta(days=8), time.min),
        current_tz,
    )
    return now, start_today, start_tomorrow, end_upcoming


def _capture_base(user):
    return CapturedProperty.objects.filter(
        owner=user,
        opportunity__isnull=True,
    ).exclude(
        status__in=(CapturedProperty.Status.VALIDATED, CapturedProperty.Status.DISCARDED),
    ).exclude(
        availability_verification_state=CapturedProperty.AvailabilityVerificationState.UNAVAILABLE,
    )


def _capture_ready_q():
    return Q(
        status__in=(CapturedProperty.Status.CAPTURED, CapturedProperty.Status.IN_REVIEW),
        availability_verification_state=CapturedProperty.AvailabilityVerificationState.CONFIRMED,
        availability_verified_at__isnull=False,
        availability_verified_by__isnull=False,
    )


def _capture_availability_q():
    return Q(
        status__in=(CapturedProperty.Status.CAPTURED, CapturedProperty.Status.IN_REVIEW),
        availability_verification_state=CapturedProperty.AvailabilityVerificationState.UNKNOWN,
    )


def _capture_review_q():
    return Q(status=CapturedProperty.Status.CAPTURED) | Q(
        review_status=CapturedProperty.ReviewStatus.PENDING,
    )


def capture_daily_queryset(user, category=None):
    """Return owner-scoped captures using the dashboard's canonical precedence."""
    qs = _capture_base(user)
    ready = _capture_ready_q()
    availability = _capture_availability_q()
    review = _capture_review_q()

    if category == "ready":
        return qs.filter(ready)
    if category == "availability":
        return qs.filter(availability)
    if category == "review":
        return qs.exclude(ready | availability).filter(review)
    if category == "duplicate":
        return qs.exclude(ready | availability | review).filter(possible_duplicate=True)
    if category is not None:
        return qs.none()

    return (
        qs.annotate(
            daily_category=Case(
                When(ready, then=Value("ready")),
                When(availability, then=Value("availability")),
                When(review, then=Value("review")),
                When(possible_duplicate=True, then=Value("duplicate")),
                default=Value(""),
                output_field=CharField(),
            ),
            daily_rank=Case(
                When(ready, then=Value(0)),
                When(availability, then=Value(1)),
                When(review, then=Value(2)),
                When(possible_duplicate=True, then=Value(3)),
                default=Value(99),
                output_field=IntegerField(),
            ),
        )
        .exclude(daily_category="")
        .select_related("source", "search_profile", "availability_verified_by")
        .order_by("daily_rank", "-captured_at", "pk")
    )


def _opportunity_base(user):
    return PropertyOpportunity.objects.filter(
        owner=user,
        captured_property__owner=user,
        status__in=ACTIVE_OPPORTUNITY_STATUSES,
    )


def opportunity_daily_queryset(user, category, now=None):
    now, start_today, start_tomorrow, end_upcoming = local_day_boundaries(now)
    qs = _opportunity_base(user)
    if category == "overdue":
        return qs.filter(next_review_at__lt=now)
    if category == "today":
        return qs.filter(next_review_at__gte=now, next_review_at__lt=start_tomorrow)
    if category == "no_next":
        return qs.filter(next_review_at__isnull=True)
    if category == "upcoming":
        return qs.filter(next_review_at__gte=start_tomorrow, next_review_at__lt=end_upcoming)
    return qs.none()


def _task_base(user):
    return (
        FollowUpTask.objects.filter(owner=user, status__in=ACTIVE_TASK_STATUSES)
        .filter(Q(property_opportunity__isnull=True) | Q(property_opportunity__owner=user))
        .filter(Q(captured_property__isnull=True) | Q(captured_property__owner=user))
        .exclude(
            task_type=FollowUpTask.TaskType.REVIEW,
            property_opportunity__isnull=False,
        )
    )


def task_daily_queryset(user, category, now=None):
    now, start_today, start_tomorrow, end_upcoming = local_day_boundaries(now)
    qs = _task_base(user)
    if category == "overdue":
        return qs.filter(due_date__lt=now)
    if category == "today":
        return qs.filter(due_date__gte=now, due_date__lt=start_tomorrow)
    if category == "upcoming":
        return qs.filter(due_date__gte=start_tomorrow, due_date__lt=end_upcoming)
    return qs.none()


def _priority_rank():
    return Case(
        When(priority=PropertyOpportunity.Priority.HIGH, then=Value(0)),
        When(priority=PropertyOpportunity.Priority.MEDIUM, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )


def _item(kind, category, obj, reason_label, due_at=None):
    return {
        "kind": kind,
        "category": category,
        "object": obj,
        "reason_label": reason_label,
        "due_at": due_at,
        "priority": getattr(obj, "priority", ""),
    }


def build_daily_workflow(user, now=None, limit=5):
    """Build a reusable, bounded and side-effect-free daily workflow snapshot."""
    now, start_today, start_tomorrow, end_upcoming = local_day_boundaries(now)

    capture_base = _capture_base(user)
    ready = _capture_ready_q()
    availability = _capture_availability_q()
    review = _capture_review_q()
    capture_counts = capture_base.aggregate(
        ready=Count("pk", filter=ready),
        availability=Count("pk", filter=availability),
        review=Count("pk", filter=~(ready | availability) & review),
        duplicate=Count("pk", filter=~(ready | availability | review) & Q(possible_duplicate=True)),
        created_today=Count("pk", filter=Q(captured_at__gte=start_today, captured_at__lt=start_tomorrow)),
        verified_today=Count(
            "pk",
            filter=Q(availability_verified_at__gte=start_today, availability_verified_at__lt=start_tomorrow),
        ),
    )
    capture_objects = list(capture_daily_queryset(user)[:limit])
    capture_items = [
        _item("capture", obj.daily_category, obj, CAPTURE_REASONS[obj.daily_category])
        for obj in capture_objects
    ]

    opportunity_base = _opportunity_base(user)
    opportunity_counts = opportunity_base.aggregate(
        overdue=Count("pk", filter=Q(next_review_at__lt=now)),
        today=Count("pk", filter=Q(next_review_at__gte=now, next_review_at__lt=start_tomorrow)),
        no_next=Count("pk", filter=Q(next_review_at__isnull=True)),
    )
    opportunity_attention = list(
        opportunity_base.annotate(
            daily_category=Case(
                When(next_review_at__lt=now, then=Value("overdue")),
                When(next_review_at__gte=now, next_review_at__lt=start_tomorrow, then=Value("today")),
                When(next_review_at__isnull=True, then=Value("no_next")),
                default=Value(""),
                output_field=CharField(),
            ),
            daily_rank=Case(
                When(next_review_at__lt=now, then=Value(0)),
                When(next_review_at__gte=now, next_review_at__lt=start_tomorrow, then=Value(1)),
                When(next_review_at__isnull=True, then=Value(2)),
                default=Value(99),
                output_field=IntegerField(),
            ),
            priority_rank=_priority_rank(),
        )
        .exclude(daily_category="")
        .select_related(
            "captured_property",
            "captured_property__source",
            "search_profile",
            "main_contact",
            "broker_company",
            "assigned_to",
        )
        .order_by("daily_rank", "priority_rank", "next_review_at", "pk")[:limit]
    )
    opportunity_items = [
        _item(
            "opportunity",
            obj.daily_category,
            obj,
            OPPORTUNITY_REASONS[obj.daily_category],
            obj.next_review_at,
        )
        for obj in opportunity_attention
    ]
    upcoming_opportunities = list(
        opportunity_daily_queryset(user, "upcoming", now)
        .annotate(priority_rank=_priority_rank())
        .select_related("captured_property", "search_profile")
        .order_by("priority_rank", "next_review_at", "pk")[:limit]
    )

    task_base = _task_base(user)
    task_counts = task_base.aggregate(
        overdue=Count("pk", filter=Q(due_date__lt=now)),
        today=Count("pk", filter=Q(due_date__gte=now, due_date__lt=start_tomorrow)),
        done_today=Count("pk", filter=Q(pk__isnull=True)),
    )
    task_counts["done_today"] = FollowUpTask.objects.filter(
        owner=user,
        status=FollowUpTask.Status.DONE,
        updated_at__gte=start_today,
        updated_at__lt=start_tomorrow,
    ).count()
    task_attention = list(
        task_base.annotate(
            daily_category=Case(
                When(due_date__lt=now, then=Value("overdue")),
                When(due_date__gte=now, due_date__lt=start_tomorrow, then=Value("today")),
                default=Value(""),
                output_field=CharField(),
            ),
            daily_rank=Case(
                When(due_date__lt=now, then=Value(0)),
                When(due_date__gte=now, due_date__lt=start_tomorrow, then=Value(1)),
                default=Value(99),
                output_field=IntegerField(),
            ),
            priority_rank=_priority_rank(),
        )
        .exclude(daily_category="")
        .select_related("property_opportunity", "captured_property", "assigned_to")
        .order_by("daily_rank", "priority_rank", "due_date", "pk")[:limit]
    )
    task_items = [
        _item("task", obj.daily_category, obj, TASK_REASONS[obj.daily_category], obj.due_date)
        for obj in task_attention
    ]
    upcoming_tasks = list(
        task_daily_queryset(user, "upcoming", now)
        .annotate(priority_rank=_priority_rank())
        .select_related("property_opportunity", "captured_property", "assigned_to")
        .order_by("priority_rank", "due_date", "pk")[:limit]
    )

    attention_rank = {
        ("opportunity", "overdue"): 0,
        ("opportunity", "today"): 1,
        ("task", "overdue"): 2,
        ("task", "today"): 3,
        ("capture", "ready"): 4,
        ("capture", "availability"): 5,
        ("capture", "review"): 6,
        ("opportunity", "no_next"): 7,
        ("capture", "duplicate"): 8,
    }
    attention = sorted(
        opportunity_items + task_items + capture_items,
        key=lambda item: (attention_rank[(item["kind"], item["category"])], item["object"].pk),
    )[:limit]
    next_actions = sorted(
        opportunity_items + task_items,
        key=lambda item: (attention_rank[(item["kind"], item["category"])], item["object"].pk),
    )[:limit]

    upcoming = [
        _item("opportunity", "upcoming", obj, OPPORTUNITY_REASONS["upcoming"], obj.next_review_at)
        for obj in upcoming_opportunities
    ] + [
        _item("task", "upcoming", obj, TASK_REASONS["upcoming"], obj.due_date)
        for obj in upcoming_tasks
    ]
    upcoming.sort(key=lambda item: (item["due_at"], item["object"].pk))

    activity_today = OpportunityActivity.objects.filter(
        opportunity__owner=user,
        created_at__gte=start_today,
        created_at__lt=start_tomorrow,
    ).count()

    counters = {
        "captures_ready": capture_counts["ready"],
        "captures_availability": capture_counts["availability"],
        "opportunities_overdue": opportunity_counts["overdue"],
        "opportunities_today": opportunity_counts["today"],
        "opportunities_no_next": opportunity_counts["no_next"],
        "tasks_overdue": task_counts["overdue"],
        "tasks_today": task_counts["today"],
    }
    return {
        "generated_at": now,
        "start_today": start_today,
        "start_tomorrow": start_tomorrow,
        "counters": counters,
        "attention_total": sum(counters.values()) + capture_counts["review"] + capture_counts["duplicate"],
        "attention": attention,
        "pending_captures": capture_items,
        "next_actions": next_actions,
        "upcoming": upcoming[:limit],
        "is_all_clear": not any(counters.values()) and not capture_counts["review"] and not capture_counts["duplicate"],
        "secondary": {
            "captures_today": capture_counts["created_today"],
            "verifications_today": capture_counts["verified_today"],
            "activities_today": activity_today,
            "tasks_done_today": task_counts["done_today"],
        },
    }
