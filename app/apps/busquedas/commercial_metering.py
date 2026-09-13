"""Race-safe commercial authorization and settlement for governed searches."""

import os
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.plans import resolve_entitlement
from apps.core.models import SearchCreditAccountPeriod, SearchCreditLedgerEntry

from .commercial_policy import billable_consumption, reservation_credits
from .models import SearchRun

_TRUE = {"1", "true", "yes", "on"}


def commercial_metering_enabled():
    configured = getattr(settings, "SOOI_SEARCH_COMMERCIAL_METERING_V1", None)
    return bool(configured) or os.environ.get(
        "SOOI_SEARCH_COMMERCIAL_METERING_V1", "0"
    ).strip().casefold() in _TRUE


def _period_dates(at=None):
    day = (at or timezone.localdate())
    start = day.replace(day=1)
    end = day.replace(day=monthrange(day.year, day.month)[1]) + timedelta(days=1)
    return start, end


def _account_for_owner(owner, *, lock=False):
    start, end = _period_dates()
    entitlement = resolve_entitlement(owner)
    allowance = Decimal(str(entitlement.limits["monthly_ai_credits"]))
    qs = SearchCreditAccountPeriod.objects
    if lock:
        qs = qs.select_for_update()
    try:
        account = qs.get(owner=owner, period_start=start)
    except SearchCreditAccountPeriod.DoesNotExist:
        try:
            # Savepoint keeps the surrounding authorization transaction usable
            # if another transaction creates this owner/period concurrently.
            with transaction.atomic():
                account = SearchCreditAccountPeriod.objects.create(
                    owner=owner, period_start=start, period_end=end,
                    allowance_credits=allowance, plan_code=entitlement.plan_code,
                )
        except IntegrityError:
            account = SearchCreditAccountPeriod.objects.select_for_update().get(
                owner=owner, period_start=start,
            )
    return account


def _totals(account):
    rows = {
        row["entry_type"]: row["total"] or Decimal("0")
        for row in account.ledger_entries.values("entry_type").annotate(total=Sum("amount_credits"))
    }
    reservations = rows.get("reservation", Decimal("0"))
    settled = rows.get("settlement", Decimal("0"))
    released = rows.get("release", Decimal("0"))
    adjustments = rows.get("adjustment", Decimal("0"))
    held = max(reservations - settled - released, Decimal("0"))
    available = account.allowance_credits + adjustments - settled - held
    return {"consumed": settled, "reserved": held, "available": available, "adjustments": adjustments}


def get_commercial_usage_summary(owner):
    with transaction.atomic():
        account = _account_for_owner(owner)
        totals = _totals(account)
    return {
        "plan_code": account.plan_code,
        "plan_name": resolve_entitlement(owner).plan["name"],
        "monthly_ai_credits": account.allowance_credits,
        "credits_used": totals["consumed"],
        "credits_reserved": totals["reserved"],
        "credits_remaining": max(totals["available"], Decimal("0")),
        "ai_discovery_cost": Decimal("1"),
        "period_start": account.period_start,
        "period_end": account.period_end,
        "is_commercial_metering": True,
        "is_internal": False,
    }


@transaction.atomic
def reserve_search_run(search_run, owner=None):
    run = SearchRun.objects.select_for_update().select_related("search_profile").get(pk=search_run.pk)
    owner = owner or run.search_profile.owner
    if owner.pk != run.search_profile.owner_id:
        raise ValidationError("La ejecución no pertenece a esta cuenta.")
    if not run.governance_enabled or run.budget_max_credits is None:
        raise ValidationError("La ejecución debe tener una autorización gobernada finita.")
    amount = reservation_credits(run.search_mode, run.budget_max_credits)
    if amount < 0 or amount > run.budget_max_credits:
        raise ValidationError("La reserva no puede superar el máximo autorizado.")
    key = f"searchrun:{run.pk}:reserve"
    existing = SearchCreditLedgerEntry.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.owner_id != owner.pk or existing.amount_credits != amount:
            raise ValidationError("La reserva idempotente no coincide con la autorización.")
        search_run.refresh_from_db()
        return existing
    account = _account_for_owner(owner, lock=True)
    if amount > _totals(account)["available"]:
        raise ValidationError("No hay suficientes Créditos de Búsqueda disponibles.")
    entry = SearchCreditLedgerEntry.objects.create(
        account_period=account, owner=owner, search_run=run,
        idempotency_key=key, entry_type="reservation", amount_credits=amount,
        reason="Autorización de búsqueda gobernada",
        metadata={"search_mode": run.search_mode},
    )
    run.budget_reserved_credits = amount
    run.full_clean()
    run.save(update_fields=["budget_reserved_credits", "updated_at"])
    search_run.budget_reserved_credits = amount
    return entry


@transaction.atomic
def settle_search_run(search_run):
    run = SearchRun.objects.select_for_update().select_related("search_profile").get(pk=search_run.pk)
    reserve = SearchCreditLedgerEntry.objects.filter(
        idempotency_key=f"searchrun:{run.pk}:reserve"
    ).first()
    if not reserve:
        return None
    settle_key = f"searchrun:{run.pk}:settle"
    existing = SearchCreditLedgerEntry.objects.filter(idempotency_key=settle_key).first()
    if existing:
        search_run.refresh_from_db()
        return existing
    consumed = billable_consumption(run)
    if consumed < 0 or consumed > reserve.amount_credits:
        raise ValidationError("El consumo debe estar dentro de la reserva autorizada.")
    settlement = SearchCreditLedgerEntry.objects.create(
        account_period=reserve.account_period, owner=reserve.owner, search_run=run,
        idempotency_key=settle_key, entry_type="settlement", amount_credits=consumed,
        reason="Consumo real de búsqueda gobernada",
    )
    unused = reserve.amount_credits - consumed
    SearchCreditLedgerEntry.objects.get_or_create(
        idempotency_key=f"searchrun:{run.pk}:release",
        defaults={
            "account_period": reserve.account_period, "owner": reserve.owner,
            "search_run": run, "entry_type": "release", "amount_credits": unused,
            "reason": "Liberación de reserva no consumida",
        },
    )
    run.budget_reserved_credits = reserve.amount_credits
    run.budget_consumed_credits = consumed
    run.full_clean()
    run.save(update_fields=["budget_reserved_credits", "budget_consumed_credits", "updated_at"])
    search_run.budget_reserved_credits = reserve.amount_credits
    search_run.budget_consumed_credits = consumed
    return settlement


def release_search_run(search_run):
    """Release only the unused hold, preserving any actual planner consumption."""
    return settle_search_run(search_run)


def update_ledger_entry(*args, **kwargs):
    raise TypeError("Commercial ledger entries are append-only.")
