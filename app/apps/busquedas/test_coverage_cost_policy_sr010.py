from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase, override_settings
from django.urls import reverse

from apps.core.models import SearchCreditLedgerEntry

from .adaptive_planner import classify_coverage_result, coverage_plan, location_batches
from .commercial_metering import reserve_search_run, settle_search_run
from .models import SearchProfile, SearchRun
from .provider_gateway import ProviderOutcome, ProviderResult
from .search_budget_ux import run_customer_context
from .searchrun_governance import initialize_search_run_governance
from .services_hybrid_coverage_v261 import SourceSpec, _ai_candidates_expanded


MALAGA_LOCATIONS = [
    "Cártama",
    "Alhaurín de la Torre",
    "Campanillas",
    "Puerto de la Torre",
    "Torremolinos",
    "Estación de Cártama",
    "Málaga",
    "Churriana",
    "Benalmádena",
]


def _strict_empty_provider(*_args, **_kwargs):
    return ProviderResult(ProviderOutcome.SUCCESS, value='{"candidates": []}')


class CoverageCostPolicySR010Tests(TestCase):
    def test_malaga_plan_keeps_four_balanced_batches(self):
        self.assertEqual(
            location_batches(MALAGA_LOCATIONS),
            [MALAGA_LOCATIONS[:3], MALAGA_LOCATIONS[3:5],
             MALAGA_LOCATIONS[5:7], MALAGA_LOCATIONS[7:]],
        )
        plan = coverage_plan(MALAGA_LOCATIONS, 1)
        self.assertEqual((plan["planned_units"], plan["planned_calls"]), (9, 4))
        self.assertEqual((plan["scheduled_units"], plan["coverage_percent_planned"]), (3, 33.33))
        self.assertEqual([item for batch in plan["batches"] for item in batch], MALAGA_LOCATIONS)

    def test_single_location_one_credit_is_full_authorization(self):
        plan = coverage_plan(["Cercedilla"], 1, planned_calls=1)
        self.assertEqual(plan["credits_required_for_full_plan"], 1)
        self.assertEqual(plan["scheduled_units"], 1)
        self.assertEqual(plan["coverage_percent_planned"], 100.0)

    def test_partial_empty_customer_copy_is_scope_safe(self):
        contract = coverage_plan(MALAGA_LOCATIONS, 1)
        contract.update({
            "accepted_candidates": 0, "executed_units": 3, "remaining_units": 6,
            "coverage_percent_executed": 33.33, "result_scope": "EXECUTED_SUBSET",
            "empty_result_classification": "PARTIAL_EMPTY_RESULT",
            "coverage_status": "limited_by_budget", "stop_reason": "budget_exhausted",
        })
        run = SimpleNamespace(
            raw_response={"coverage_contract": contract}, total_valid_candidates=0,
            search_mode="eco", coverage_status="limited_by_budget", stop_reason="budget_exhausted",
            governance_enabled=True,
        )
        context = run_customer_context(run)
        self.assertIn("0 resultados en la cobertura ejecutada", context["result_summary"])
        self.assertEqual(context["result_summary"].count("0 resultados en la cobertura ejecutada"), 1)
        self.assertEqual(
            (contract["executed_units"], contract["planned_units"],
             contract["coverage_percent_executed"], contract["remaining_units"],
             contract["coverage_status"].upper(), contract["stop_reason"].upper(),
             contract["empty_result_classification"], contract["result_scope"]),
            (3, 9, 33.33, 6, "LIMITED_BY_BUDGET", "BUDGET_EXHAUSTED",
             "PARTIAL_EMPTY_RESULT", "EXECUTED_SUBSET"),
        )

    def test_full_empty_requires_conclusive_full_plan(self):
        plan = coverage_plan(MALAGA_LOCATIONS, 4)
        result = classify_coverage_result(plan, executed_units=9, accepted_candidates=0)
        self.assertEqual(result["empty_result_classification"], "FULL_PLAN_EMPTY_RESULT")
        self.assertEqual((result["remaining_units"], result["coverage_percent_executed"]), (0, 100.0))
        self.assertEqual((result["coverage_status"], result["result_scope"]), ("full", "FULL_SOOI_PLAN"))

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_provider_or_format_degradation_can_never_be_full_empty(self, provider):
        provider.return_value = ProviderResult(ProviderOutcome.HARD_FAILURE, reason="fixture_outage")
        charged, trace = Mock(return_value=True), []
        _ai_candidates_expanded(
            SourceSpec("fixture", (), "openai_web_search"),
            {"location_scope": "multi_location", "search_locations": MALAGA_LOCATIONS},
            max_results=9, before_call=charged, batch_trace=trace,
        )
        self.assertEqual((provider.call_count, charged.call_count), (1, 1))
        self.assertEqual(trace[0]["outcome"], ProviderOutcome.HARD_FAILURE.value)
        plan = coverage_plan(["A"], 1)
        for reason in ("provider_outage", "format_noncompliance"):
            result = classify_coverage_result(
                plan, executed_units=0, accepted_candidates=0, degraded=True, stop_reason=reason,
            )
            self.assertEqual(result["empty_result_classification"], "PARTIAL_EMPTY_RESULT")
            self.assertEqual(result["result_scope"], "EXECUTED_SUBSET")

    def test_adaptive_stop_with_units_left_is_partial(self):
        plan = coverage_plan(MALAGA_LOCATIONS, 4)
        result = classify_coverage_result(
            plan, executed_units=3, accepted_candidates=1, stop_reason="sufficient_coverage",
        )
        self.assertFalse(result["is_full_plan"])
        self.assertEqual(result["remaining_units"], 6)

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_hidden_later_batch_candidate_proves_one_credit_empty_is_not_full_empty(self, provider):
        hidden = {
            "source_url": "https://www.idealista.com/inmueble/9/", "price": 100000,
            "municipality": "Benalmádena", "bedrooms": 2,
            "property_type": "flat", "area_m2": 70,
        }
        responses = [
            ProviderResult(ProviderOutcome.SUCCESS, value='{"candidates": []}'),
            ProviderResult(ProviderOutcome.SUCCESS, value='{"candidates": []}'),
            ProviderResult(ProviderOutcome.SUCCESS, value='{"candidates": []}'),
            ProviderResult(ProviderOutcome.SUCCESS, value='{"candidates": [%s]}' % __import__("json").dumps(hidden)),
        ]
        spec = SourceSpec("idealista", ("idealista.com",), "openai_web_search")

        provider.side_effect = responses
        one_credit_gate = Mock(side_effect=[True, False])
        subset, subset_error = _ai_candidates_expanded(
            spec,
            {"location_scope": "multi_location", "search_locations": MALAGA_LOCATIONS},
            max_results=9, before_call=one_credit_gate,
        )
        self.assertEqual(subset, [])
        self.assertIn("planner_limit", subset_error)
        self.assertEqual(provider.call_count, 1)

        provider.reset_mock(side_effect=True)
        provider.side_effect = responses
        trace = []
        candidates, error = _ai_candidates_expanded(
            spec,
            {"location_scope": "multi_location", "search_locations": MALAGA_LOCATIONS},
            max_results=9, batch_trace=trace,
        )
        self.assertIsNone(error)
        self.assertEqual(provider.call_count, 4)
        self.assertEqual(candidates[0]["municipality"], "Benalmádena")
        one_credit = classify_coverage_result(
            coverage_plan(MALAGA_LOCATIONS, 1), executed_units=3, accepted_candidates=0,
            stop_reason="budget_exhausted",
        )
        self.assertEqual(one_credit["empty_result_classification"], "PARTIAL_EMPTY_RESULT")

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search", side_effect=_strict_empty_provider)
    def test_full_plan_four_call_completion_is_full(self, provider):
        trace = []
        _ai_candidates_expanded(
            SourceSpec("fixture", (), "openai_web_search"),
            {"location_scope": "multi_location", "search_locations": MALAGA_LOCATIONS},
            max_results=9, batch_trace=trace,
        )
        self.assertEqual(provider.call_count, 4)
        self.assertTrue(all(item["format_conclusive"] for item in trace))
        result = classify_coverage_result(
            coverage_plan(MALAGA_LOCATIONS, 4), executed_units=9, accepted_candidates=0,
            stop_reason="completed_plan",
        )
        self.assertEqual(
            (result["coverage_percent_executed"], result["remaining_units"], result["coverage_status"],
             result["empty_result_classification"]),
            (100.0, 0, "full", "FULL_PLAN_EMPTY_RESULT"),
        )

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_strict_format_noncompliance_is_unresolved_and_charged_once(self, provider):
        provider.return_value = ProviderResult(ProviderOutcome.SUCCESS, value="not strict json")
        charged = Mock(return_value=True)
        trace, counters = [], {"format_noncompliance_count": 0}
        _ai_candidates_expanded(
            SourceSpec("fixture", (), "openai_web_search"),
            {"location_scope": "municipality", "search_locations": ["Málaga"]},
            max_results=1, before_call=charged, stage_trace=counters, batch_trace=trace,
        )
        self.assertEqual((provider.call_count, charged.call_count), (1, 1))
        self.assertEqual(counters["format_noncompliance_count"], 1)
        self.assertFalse(trace[0]["format_conclusive"])
        result = classify_coverage_result(
            coverage_plan(["Málaga"], 1, planned_calls=1), executed_units=0,
            accepted_candidates=0, degraded=True, stop_reason="format_noncompliance",
        )
        self.assertEqual(result["empty_result_classification"], "PARTIAL_EMPTY_RESULT")


@override_settings(SOOI_SEARCH_COMMERCIAL_METERING_V1=True)
class CoverageCostGovernanceSR010Tests(DjangoTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sr010", password="x")
        self.profile = SearchProfile.objects.create(
            owner=self.user, name="SR0.10 Málaga", province="Málaga", zone="Málaga",
        )
        self.client.force_login(self.user)

    def governed_run(self, maximum="1", **values):
        run = SearchRun.objects.create(
            search_profile=self.profile, execution_mode="ai_discovery",
        )
        initialize_search_run_governance(
            run, self.profile, search_mode=SearchRun.SearchMode.ECO,
            budget_max_credits=Decimal(maximum), require_feature_flag=False,
        )
        if values:
            for field, value in values.items():
                setattr(run, field, value)
            run.full_clean()
            run.save(update_fields=[*values, "updated_at"])
        return run

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_replay_creates_no_call_charge_or_coverage_mutation(self, provider):
        from .tasks import run_search_profile_task

        run = self.governed_run()
        reserve_search_run(run)
        run.calls_attempted = 1
        run.calls_succeeded = 1
        run.budget_consumed_credits = Decimal("1")
        run.status = SearchRun.Status.COMPLETED
        run.raw_response = {
            "coverage_contract": {"executed_units": 3},
            "source_coverage": [],
        }
        run.save(update_fields=[
            "calls_attempted", "calls_succeeded", "budget_consumed_credits",
            "status", "raw_response", "updated_at",
        ])
        settle_search_run(run)
        before = (run.commercial_credit_entries.count(), run.raw_response, run.calls_attempted)
        self.assertEqual(run_search_profile_task.run(self.profile.pk, run.pk), run.pk)
        settle_search_run(run)
        run.refresh_from_db()
        self.assertEqual((run.commercial_credit_entries.count(), run.raw_response, run.calls_attempted), before)
        provider.assert_not_called()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"}, clear=False)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    @patch("apps.busquedas.views.run_search_profile_task.delay")
    def test_insufficient_balance_creates_no_queue_call_or_charge(
        self, delay, provider, _permitted,
    ):
        reserve_search_run(self.governed_run(maximum="50"))
        ledger_before = SearchCreditLedgerEntry.objects.filter(owner=self.user).count()
        response = self.client.post(reverse("searchprofile_execute", args=[self.profile.pk]), {
            "search_mode": "eco", "budget_max_credits": "1",
        })
        self.assertEqual(response.status_code, 302)
        delay.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(SearchCreditLedgerEntry.objects.filter(owner=self.user).count(), ledger_before)

    def test_qualifying_reuse_costs_zero_and_does_not_inflate_coverage(self):
        source = self.governed_run(maximum="1", raw_response={"coverage_contract": {
            "planned_units": 9, "executed_units": 3, "coverage_percent_executed": 33.33,
        }})
        current = self.governed_run(maximum="1", reused_from_search_run=source,
                           raw_response={"coverage_contract": dict(source.raw_response["coverage_contract"])})
        reserve_search_run(current)
        provider = Mock()
        settle_search_run(current)
        provider.assert_not_called()
        current.refresh_from_db()
        self.assertEqual(current.commercial_credit_entries.get(entry_type="settlement").amount_credits, 0)
        self.assertEqual(current.raw_response["coverage_contract"]["executed_units"], 3)

    def test_one_credit_is_one_attempted_governed_external_batch(self):
        run = self.governed_run()
        reserve_search_run(run)
        run.calls_attempted = 1
        run.calls_succeeded = 0
        run.budget_consumed_credits = Decimal("1")
        run.save(update_fields=["calls_attempted", "calls_succeeded", "budget_consumed_credits"])
        settle_search_run(run)
        self.assertEqual(run.commercial_credit_entries.get(entry_type="settlement").amount_credits, 1)

    def test_full_plan_that_requires_one_credit_is_full_after_success(self):
        plan = coverage_plan(["Cercedilla"], 1, planned_calls=1)
        result = classify_coverage_result(
            plan, executed_units=1, accepted_candidates=1, stop_reason="completed_plan",
        )
        self.assertEqual((plan["credits_required_for_full_plan"], plan["coverage_percent_planned"]), (1, 100.0))
        self.assertEqual((result["coverage_status"], result["coverage_percent_executed"]), ("full", 100.0))
