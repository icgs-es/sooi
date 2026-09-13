from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.core.plans import SOOI_PLANS, resolve_entitlement
from apps.ia.usage import AI_DISCOVERY_CREDITS, get_ai_usage_summary

from .commercial_metering import (
    commercial_metering_enabled, get_commercial_usage_summary,
    reserve_search_run, settle_search_run, update_ledger_entry,
)
from .commercial_policy import billable_consumption, reservation_credits
from apps.core.models import SearchCreditAccountPeriod, SearchCreditLedgerEntry
from .models import SearchProfile, SearchRun


@override_settings(SOOI_SEARCH_COMMERCIAL_METERING_V1=True)
class CommercialMeteringSR03CTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meter", password="x")
        self.profile = SearchProfile.objects.create(
            owner=self.user, name="Medición", province="Córdoba", zone="Centro",
        )

    def governed_run(self, maximum="3", mode="eco", owner=None):
        profile = self.profile
        if owner is not None:
            profile = SearchProfile.objects.create(owner=owner, name="Otra", province="Jaén")
        return SearchRun.objects.create(
            search_profile=profile, execution_mode="ai_discovery", governance_enabled=True,
            governance_version="1", search_mode=mode, budget_max_credits=Decimal(maximum),
            budget_reserved_credits=Decimal("0"), budget_consumed_credits=Decimal("0"),
        )

    @override_settings(SOOI_SEARCH_COMMERCIAL_METERING_V1=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_t01_feature_flag_defaults_off(self):
        self.assertFalse(commercial_metering_enabled())

    @override_settings(SOOI_SEARCH_COMMERCIAL_METERING_V1=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_t02_flag_off_preserves_legacy_usage_source(self):
        SearchRun.objects.create(search_profile=self.profile, execution_mode="ai_discovery")
        self.assertEqual(get_ai_usage_summary(self.user)["credits_used"], AI_DISCOVERY_CREDITS)

    def test_t03_current_plan_allowance_is_reused_not_duplicated(self):
        summary = get_commercial_usage_summary(self.user)
        self.assertEqual(summary["monthly_ai_credits"], resolve_entitlement(self.user).limits["monthly_ai_credits"])

    def test_t04_governed_paid_mode_reserves_before_task_queue(self):
        run = self.governed_run(); reserve_search_run(run)
        self.assertEqual(run.budget_reserved_credits, Decimal("3"))

    def test_t05_failed_reservation_queues_no_task(self):
        run = self.governed_run(maximum="51")
        with self.assertRaises(ValidationError): reserve_search_run(run)
        self.assertFalse(SearchCreditLedgerEntry.objects.exists())

    def test_t06_free_zero_budget_requires_no_positive_reservation(self):
        entry = reserve_search_run(self.governed_run(maximum="0", mode="free"))
        self.assertEqual(entry.amount_credits, 0)

    def test_t07_reservation_is_idempotent_for_same_searchrun(self):
        run = self.governed_run(); self.assertEqual(reserve_search_run(run).pk, reserve_search_run(run).pk)

    def test_t08_duplicate_reservation_database_key_is_rejected(self):
        run = self.governed_run(); entry = reserve_search_run(run)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SearchCreditLedgerEntry.objects.create(account_period=entry.account_period, owner=self.user, search_run=run, idempotency_key=entry.idempotency_key, entry_type="reservation", amount_credits=1)

    def test_t09_reserved_never_exceeds_run_max(self):
        run = self.governed_run("2"); reserve_search_run(run); self.assertLessEqual(run.budget_reserved_credits, run.budget_max_credits)

    def test_t10_available_balance_cannot_go_negative(self):
        run = self.governed_run("50"); reserve_search_run(run)
        with self.assertRaises(ValidationError): reserve_search_run(self.governed_run("1"))

    def test_t11_two_reservations_cannot_overspend_same_allowance(self):
        reserve_search_run(self.governed_run("30"))
        with self.assertRaises(ValidationError): reserve_search_run(self.governed_run("30"))

    def test_t12_settlement_consumes_actual_not_authorized_max(self):
        run = self.governed_run("3"); reserve_search_run(run); SearchRun.objects.filter(pk=run.pk).update(budget_consumed_credits=1); settle_search_run(run)
        self.assertEqual(get_commercial_usage_summary(self.user)["credits_used"], 1)

    def test_t13_settlement_is_idempotent(self):
        run = self.governed_run(); reserve_search_run(run); self.assertEqual(settle_search_run(run).pk, settle_search_run(run).pk)

    def test_t14_unused_reservation_is_released(self):
        run = self.governed_run(); reserve_search_run(run); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.get(entry_type="release").amount_credits, 3)

    def test_t15_release_is_idempotent(self):
        run = self.governed_run(); reserve_search_run(run); settle_search_run(run); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.filter(entry_type="release").count(), 1)

    def test_t16_partial_consumption_releases_unused(self):
        run = self.governed_run("3"); reserve_search_run(run); SearchRun.objects.filter(pk=run.pk).update(budget_consumed_credits=2); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.get(entry_type="release").amount_credits, 1)

    def test_t17_hard_provider_failure_does_not_charge_skipped_calls(self):
        run = self.governed_run("3"); reserve_search_run(run); SearchRun.objects.filter(pk=run.pk).update(budget_consumed_credits=1, stop_reason="provider_hard_failure"); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.get(entry_type="settlement").amount_credits, 1)

    def test_t18_pure_reuse_consumes_zero_external_search_credits(self):
        run = self.governed_run(); reserve_search_run(run); run.reused_from_search_run = self.governed_run(); run.save(); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.get(entry_type="settlement").amount_credits, 0)

    def test_t19_write_after_analyze_consumes_zero_additional_credits(self):
        run = self.governed_run(); reserve_search_run(run); settle_search_run(run); before = SearchCreditLedgerEntry.objects.count(); settle_search_run(run)
        self.assertEqual(SearchCreditLedgerEntry.objects.count(), before)

    def test_t20_celery_replay_does_not_double_settle(self):
        run = self.governed_run(); reserve_search_run(run); settle_search_run(run); settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.filter(entry_type="settlement").count(), 1)

    def test_t21_expand_creates_independent_new_reservation(self):
        old, new = self.governed_run("1"), self.governed_run("2", "amplia"); reserve_search_run(old); reserve_search_run(new)
        self.assertNotEqual(old.commercial_credit_entries.get(entry_type="reservation").pk, new.commercial_credit_entries.get(entry_type="reservation").pk)

    def test_t22_old_run_authorization_is_not_mutated_by_expand(self):
        old = self.governed_run("1"); reserve_search_run(old); reserve_search_run(self.governed_run("2", "amplia")); old.refresh_from_db(); self.assertEqual(old.budget_max_credits, 1)

    def test_t23_cross_owner_cannot_reserve_against_other_account(self):
        other = get_user_model().objects.create_user(username="other")
        with self.assertRaises(ValidationError): reserve_search_run(self.governed_run(), other)

    def test_t24_customer_balance_shows_one_canonical_source(self):
        summary = get_commercial_usage_summary(self.user); self.assertTrue(summary["is_commercial_metering"])

    def test_t25_customer_balance_does_not_show_internal_provider_cost(self):
        self.assertNotIn("actual_internal_cost", get_commercial_usage_summary(self.user))

    def test_t26_customer_balance_does_not_show_openai_tokens_or_model(self):
        self.assertTrue({"tokens", "model_name"}.isdisjoint(get_commercial_usage_summary(self.user)))

    def test_t27_ledger_entries_are_append_only_through_service_api(self):
        with self.assertRaises(TypeError): update_ledger_entry(pk=1, amount_credits=9)

    def test_t28_ledger_idempotency_key_is_unique(self):
        self.assertTrue(SearchCreditLedgerEntry._meta.get_field("idempotency_key").unique)

    def test_t29_historical_unmetered_run_does_not_create_fake_ledger_usage(self):
        SearchRun.objects.create(search_profile=self.profile, execution_mode="ai_discovery")
        self.assertEqual(SearchCreditLedgerEntry.objects.count(), 0)

    def test_t30_settlement_preserves_consumed_lte_reserved_lte_max(self):
        run = self.governed_run("3"); reserve_search_run(run); SearchRun.objects.filter(pk=run.pk).update(budget_consumed_credits=2); settle_search_run(run); run.refresh_from_db()
        self.assertLessEqual(run.budget_consumed_credits, run.budget_reserved_credits); self.assertLessEqual(run.budget_reserved_credits, run.budget_max_credits)

    def test_t31_task_retry_before_terminal_state_does_not_double_reserve(self):
        run = self.governed_run(); reserve_search_run(run); reserve_search_run(run); self.assertEqual(run.commercial_credit_entries.filter(entry_type="reservation").count(), 1)

    def test_t32_terminal_failure_before_spend_releases_reservation(self):
        run = self.governed_run(); reserve_search_run(run); run.status="failed"; run.save(); settle_search_run(run)
        self.assertEqual(get_commercial_usage_summary(self.user)["credits_reserved"], 0)

    def test_t33_commercial_policy_is_provider_independent(self):
        self.assertEqual(reservation_credits("eco", 3), 3)

    def test_t34_no_flat_legacy_ten_credit_charge_for_metered_governed_run(self):
        run = self.governed_run("3"); reserve_search_run(run); settle_search_run(run)
        self.assertNotEqual(get_commercial_usage_summary(self.user)["credits_used"], AI_DISCOVERY_CREDITS)

    def test_t35_no_second_credit_account_model_or_balance_source(self):
        get_commercial_usage_summary(self.user); self.assertEqual(SearchCreditAccountPeriod.objects.filter(owner=self.user).count(), 1)

    def test_t36_no_city_or_portal_special_case_in_metering_policy(self):
        text = Path(__file__).with_name("commercial_policy.py").read_text()
        self.assertNotIn("Córdoba", text); self.assertNotIn("portal", text.casefold())
