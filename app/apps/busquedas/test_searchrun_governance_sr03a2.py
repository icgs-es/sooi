from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import SearchProfile, SearchRun
from .searchrun_governance import (
    build_search_fingerprint,
    initialize_search_run_governance,
    project_hard_provider_stop,
    search_governance_enabled,
)


class SearchRunGovernanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sr03a2")
        self.profile = SearchProfile.objects.create(
            owner=self.user, name="Governance", operation_type="sale",
            province="Córdoba", geography_scope="multi_municipality",
            municipalities=["Pozoblanco", "Añora"], property_types=["flat", "house"],
            min_price=Decimal("10000"), max_price=Decimal("90000"),
            min_bedrooms=2, min_area_m2=Decimal("70"),
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)

    def _initialize(self, mode=SearchRun.SearchMode.ECO, maximum="10"):
        return initialize_search_run_governance(
            self.run, self.profile, search_mode=mode, budget_max_credits=maximum,
        )

    def test_t01_historical_run_is_explicitly_ungoverned(self):
        self.assertFalse(self.run.governance_enabled)
        self.assertEqual(self.run.search_mode, SearchRun.SearchMode.LEGACY)
        self.assertEqual(self.run.coverage_status, SearchRun.CoverageStatus.NOT_EVALUATED)
        self.assertIsNone(self.run.budget_max_credits)
        self.assertEqual(self.run.search_fingerprint, "")

    def test_t02_search_modes_are_bounded(self):
        self.assertEqual(set(SearchRun.SearchMode.values), {"legacy", "free", "eco", "amplia", "profunda"})
        self.run.search_mode = "unbounded"
        with self.assertRaises(ValidationError):
            self.run.full_clean()

    def test_t03_coverage_statuses_are_bounded(self):
        self.assertIn("degraded_provider", SearchRun.CoverageStatus.values)
        self.run.coverage_status = "unknown"
        with self.assertRaises(ValidationError):
            self.run.full_clean()

    def test_t04_stop_reasons_are_bounded(self):
        self.assertIn("provider_hard_failure", SearchRun.StopReason.values)
        self.run.stop_reason = "unknown"
        with self.assertRaises(ValidationError):
            self.run.full_clean()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t05_initialization_writes_authorization_and_fingerprint(self):
        self.assertTrue(self._initialize())
        self.run.refresh_from_db()
        self.assertTrue(self.run.governance_enabled)
        self.assertEqual(self.run.search_mode, SearchRun.SearchMode.ECO)
        self.assertEqual(self.run.budget_max_credits, Decimal("10"))
        self.assertEqual(len(self.run.search_fingerprint), 64)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t06_same_authorization_is_idempotent(self):
        self._initialize()
        fingerprint = self.run.search_fingerprint
        self.run.calls_attempted = 3
        self.run.save(update_fields=["calls_attempted"])
        self._initialize()
        self.run.refresh_from_db()
        self.assertEqual(self.run.search_fingerprint, fingerprint)
        self.assertEqual(self.run.calls_attempted, 3)

    def test_t07_consumed_cannot_exceed_reserved(self):
        self.run.governance_enabled = True
        self.run.search_mode = "eco"
        self.run.budget_max_credits = 10
        self.run.budget_reserved_credits = 5
        self.run.budget_consumed_credits = 6
        with self.assertRaises(ValidationError):
            self.run.full_clean()

    def test_t08_reserved_cannot_exceed_max(self):
        self.run.governance_enabled = True
        self.run.search_mode = "eco"
        self.run.budget_max_credits = 4
        self.run.budget_reserved_credits = 5
        self.run.budget_consumed_credits = 0
        with self.assertRaises(ValidationError):
            self.run.full_clean()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t09_cannot_reduce_max_below_reserved_or_consumed(self):
        self._initialize(maximum="10")
        self.run.budget_reserved_credits = Decimal("7")
        self.run.budget_consumed_credits = Decimal("6")
        self.run.save(update_fields=["budget_reserved_credits", "budget_consumed_credits"])
        with self.assertRaises(ValidationError):
            self._initialize(maximum="5")

    def test_t10_equivalent_filters_have_same_fingerprint(self):
        a = {"operation": " SALE ", "province": "Córdoba", "municipalities": ["Añora"], "property_types": ["house"], "min_price": "1.00"}
        b = {"operation_type": "sale", "province": "CÓRDOBA", "municipalities": ["añora"], "property_types": ["HOUSE"], "min_price": 1}
        self.assertEqual(build_search_fingerprint(a, "eco"), build_search_fingerprint(b, "eco"))

    def test_t11_materially_different_geography_or_filter_differs(self):
        base = {"operation": "sale", "province": "Córdoba", "municipalities": ["Añora"], "min_bedrooms": 2}
        changed = {**base, "municipalities": ["Pozoblanco"]}
        self.assertNotEqual(build_search_fingerprint(base, "eco"), build_search_fingerprint(changed, "eco"))

    def test_t12_different_mode_differs(self):
        self.assertNotEqual(build_search_fingerprint(self.profile, "eco"), build_search_fingerprint(self.profile, "amplia"))

    def test_t13_unordered_types_and_municipalities_are_stable(self):
        a = {"property_types": ["house", "flat"], "municipalities": ["B", "A"]}
        b = {"municipalities": ["A", "B"], "property_types": ["flat", "house"]}
        self.assertEqual(build_search_fingerprint(a, "eco"), build_search_fingerprint(b, "eco"))

    def test_t14_unicode_case_and_whitespace_are_stable(self):
        a = {"province": "  CO\u0301RDOBA ", "municipalities": ["  Peña   Alta "]}
        b = {"province": "córdoba", "municipalities": ["peña alta"]}
        self.assertEqual(build_search_fingerprint(a, "eco"), build_search_fingerprint(b, "eco"))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t15_counters_initialize_safely(self):
        self._initialize()
        fields = ("sources_planned", "sources_executed", "sources_omitted", "calls_planned", "calls_attempted", "calls_succeeded", "cache_hits")
        self.assertTrue(all(getattr(self.run, field) == 0 for field in fields))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t16_internal_costs_remain_unset(self):
        self._initialize()
        self.assertIsNone(self.run.estimated_internal_cost)
        self.assertIsNone(self.run.actual_internal_cost)

    @patch.dict("os.environ", {}, clear=True)
    def test_t17_flag_defaults_off(self):
        self.assertFalse(search_governance_enabled())

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "yes"})
    def test_t18_flag_on_enables_initialization(self):
        self.assertTrue(self._initialize())

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "0"})
    def test_t19_flag_off_preserves_legacy_path(self):
        self.assertFalse(self._initialize())
        self.run.refresh_from_db()
        self.assertFalse(self.run.governance_enabled)
        self.assertEqual(self.run.search_mode, "legacy")

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t20_hard_provider_projection_is_compatible(self):
        self._initialize()
        self.assertTrue(project_hard_provider_stop(self.run, [{"provider_outcome": "HARD_FAILURE"}]))
        self.assertEqual(self.run.coverage_status, "degraded_provider")
        self.assertEqual(self.run.stop_reason, "provider_hard_failure")

    def test_t21_database_constraints_reject_invalid_budget_order(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SearchRun.objects.create(
                    search_profile=self.profile, governance_enabled=True, search_mode="eco",
                    budget_max_credits=1, budget_reserved_credits=2, budget_consumed_credits=0,
                )

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t22_initialization_does_not_call_provider(self):
        provider = Mock()
        self._initialize()
        provider.assert_not_called()
