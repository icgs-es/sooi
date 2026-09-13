from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from .adaptive_planner import (
    AdaptivePlanner, MODE_POLICIES, SourceTier, ordered_sources,
    planner_for_run, sufficient_coverage, valid_unique_candidates,
)
from .provider_gateway import ProviderCircuit, ProviderOutcome, ProviderResult
from .services_hybrid_coverage_v261 import SourceSpec, _ai_candidates_expanded


def specs():
    return [
        SourceSpec("det-b", ("b.test",), "deterministic", priority=20),
        SourceSpec("expand-1", ("e1.test",), "openai_web_search", tier="ai_expansion", priority=10),
        SourceSpec("efficient-1", ("a1.test",), "openai_web_search", tier="ai_efficient", priority=10),
        SourceSpec("det-a", ("a.test",), "deterministic", priority=10),
        SourceSpec("efficient-2", ("a2.test",), "openai_web_search", tier="ai_efficient", priority=20),
        SourceSpec("expand-2", ("e2.test",), "openai_web_search", tier="ai_expansion", priority=20),
        SourceSpec("expand-3", ("e3.test",), "openai_web_search", tier="ai_expansion", priority=30),
        SourceSpec("expand-4", ("e4.test",), "openai_web_search", tier="ai_expansion", priority=40),
        SourceSpec("expand-5", ("e5.test",), "openai_web_search", tier="ai_expansion", priority=50),
        SourceSpec("expand-6", ("e6.test",), "openai_web_search", tier="ai_expansion", priority=60),
    ]


def rows(count, source="source", classification="verified", duplicate=False):
    return [{"source": source, "candidates": [
        {"source_url": "https://example.test/1" if duplicate else f"https://example.test/{i}",
         "classification": classification} for i in range(count)
    ]}]


def run_stub(**overrides):
    values = dict(
        governance_enabled=True, search_mode="eco", budget_max_credits=10,
        budget_reserved_credits=0, budget_consumed_credits=0,
        sources_planned=0, sources_executed=0, sources_omitted=0,
        calls_planned=0, calls_attempted=0, calls_succeeded=0, cache_hits=0,
        coverage_status="not_evaluated", stop_reason="none",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class AdaptivePlannerSR03A3Tests(TestCase):
    def test_t01_free_executes_no_ai_backed_source(self):
        self.assertTrue(all(s.method == "deterministic" for s in AdaptivePlanner("free", specs()).allowed))

    def test_t02_deterministic_sources_execute_before_ai(self):
        ordered = ordered_sources(specs())
        self.assertLess(max(i for i, s in enumerate(ordered) if s.method == "deterministic"),
                        min(i for i, s in enumerate(ordered) if s.method != "deterministic"))

    def test_t03_deterministic_sufficiency_prevents_ai(self):
        self.assertTrue(sufficient_coverage("eco", rows(3)))

    def test_t04_eco_stops_after_early_ai_coverage(self):
        planner = AdaptivePlanner("eco", specs())
        planner.stop("sufficient_coverage", planner.allowed[3:])
        self.assertEqual(planner.stop_reason, "sufficient_coverage")

    def test_t05_amplia_is_broader_than_eco(self):
        self.assertGreater(len(AdaptivePlanner("amplia", specs()).allowed), len(AdaptivePlanner("eco", specs()).allowed))

    def test_t06_profunda_has_finite_hard_cap(self):
        policy = MODE_POLICIES["profunda"]
        self.assertLessEqual(len([s for s in AdaptivePlanner("profunda", specs()).allowed if s.method != "deterministic"]), policy.max_ai_sources)
        self.assertLess(policy.max_ai_calls, 100)

    def test_t07_aggregate_call_ceiling_across_sources(self):
        planner = AdaptivePlanner("eco", specs(), budget_max=100)
        accepted = sum(planner.before_external_call() for _ in range(20))
        self.assertEqual(accepted, MODE_POLICIES["eco"].max_ai_calls)

    def test_t08_budget_prevents_next_external_call(self):
        planner = AdaptivePlanner("profunda", specs(), budget_max=1)
        self.assertTrue(planner.before_external_call())
        self.assertFalse(planner.before_external_call())

    def test_t09_budget_sets_stop_reason(self):
        planner = AdaptivePlanner("profunda", specs(), budget_max=0)
        planner.before_external_call()
        self.assertEqual(planner.stop_reason, "budget_exhausted")

    def test_t10_budget_projects_limited_status(self):
        planner, run = AdaptivePlanner("eco", specs(), budget_max=0), run_stub()
        planner.before_external_call(); planner.apply_to_run(run, rows(1))
        self.assertEqual(run.coverage_status, "limited_by_budget")

    def test_t11_plan_omissions_project_limited_by_plan(self):
        planner, run = AdaptivePlanner("free", specs()), run_stub(search_mode="free")
        planner.record_executed(planner.allowed[0]); planner.apply_to_run(run, rows(1))
        self.assertEqual(run.coverage_status, "limited_by_plan")

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t12_hard_circuit_prevents_remaining_calls(self, provider):
        provider.return_value = ProviderResult(ProviderOutcome.HARD_FAILURE, reason="quota")
        _ai_candidates_expanded(SourceSpec("ai", ("x.test",), "openai_web_search"),
                                {"location_scope": "multi_location", "search_locations": list("ABCDEFGH")}, 8,
                                circuit=ProviderCircuit("openai"))
        self.assertEqual(provider.call_count, 1)

    def test_t13_hard_circuit_projects_degraded_provider(self):
        planner, run = AdaptivePlanner("eco", specs()), run_stub()
        planner.provider_degraded = True; planner.stop("provider_hard_failure"); planner.apply_to_run(run, rows(1))
        self.assertEqual(run.coverage_status, "degraded_provider")

    def test_t14_sufficient_coverage_stop_is_durable(self):
        planner, run = AdaptivePlanner("eco", specs()), run_stub()
        planner.stop("sufficient_coverage"); planner.apply_to_run(run, rows(3))
        self.assertEqual(run.stop_reason, "sufficient_coverage")

    def test_t15_no_results_only_describes_executed_coverage(self):
        planner, run = AdaptivePlanner("free", specs()), run_stub(search_mode="free")
        planner.apply_to_run(run, [])
        self.assertEqual(run.coverage_status, "no_results")
        self.assertGreater(run.sources_omitted, 0)

    def test_t16_discarded_candidates_do_not_satisfy_target(self):
        self.assertFalse(sufficient_coverage("eco", rows(20, classification="discarded")))

    def test_t17_duplicates_do_not_inflate_coverage(self):
        self.assertEqual(valid_unique_candidates(rows(20, duplicate=True))[0], 1)
        self.assertFalse(sufficient_coverage("eco", rows(20, duplicate=True)))

    def test_t18_source_counters_are_coherent(self):
        planner, run = AdaptivePlanner("free", specs()), run_stub(search_mode="free")
        for item in planner.allowed: planner.record_executed(item)
        planner.apply_to_run(run, rows(1))
        self.assertEqual(run.sources_planned, run.sources_executed + run.sources_omitted)

    def test_t19_call_counters_are_coherent(self):
        planner, run = AdaptivePlanner("eco", specs(), budget_max=10), run_stub()
        planner.before_external_call(); planner.after_external_call(True); planner.apply_to_run(run, rows(1))
        self.assertLessEqual(run.calls_succeeded, run.calls_attempted)
        self.assertLessEqual(run.calls_attempted, run.calls_planned)

    def test_t20_reentry_does_not_double_bookkeep(self):
        planner, run = AdaptivePlanner("eco", specs(), budget_max=10), run_stub()
        planner.record_executed(planner.allowed[0]); planner.before_external_call(); planner.apply_to_run(run, rows(1))
        before = (run.sources_executed, run.calls_attempted, run.budget_consumed_credits)
        planner.apply_to_run(run, rows(1))
        self.assertEqual(before, (run.sources_executed, run.calls_attempted, run.budget_consumed_credits))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "0"})
    def test_t21_flag_off_preserves_legacy_all_source_behavior(self):
        self.assertIsNone(planner_for_run(run_stub(), specs()))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t22_flag_on_only_activates_for_governed_runs(self):
        self.assertIsNotNone(planner_for_run(run_stub(), specs()))
        self.assertIsNone(planner_for_run(run_stub(governance_enabled=False), specs()))

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t23_four_batch_eight_source_contract_is_one_invocation(self, provider):
        provider.return_value = ProviderResult(ProviderOutcome.HARD_FAILURE, reason="billing")
        circuit = ProviderCircuit("openai")
        for i in range(8):
            _ai_candidates_expanded(SourceSpec(str(i), ("x.test",), "openai_web_search"),
                                    {"location_scope": "multi_location", "search_locations": list("ABCDEFGHIJKLMNOP")},
                                    8, circuit=circuit)
        self.assertEqual(provider.call_count, 1)

    def test_t24_planning_does_not_call_provider(self):
        provider = Mock()
        AdaptivePlanner("profunda", specs())
        provider.assert_not_called()

    def test_t25_planner_requires_no_city_or_url_special_case(self):
        generic = [SourceSpec("generic", ("invalid.test",), "openai_web_search", tier="ai_efficient")]
        self.assertEqual(AdaptivePlanner("eco", generic).allowed, generic)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t26_full_commercial_hold_does_not_count_as_call_spend(self):
        run = run_stub(
            search_mode="profunda", budget_max_credits=1,
            budget_reserved_credits=1, budget_consumed_credits=0,
        )
        planner = planner_for_run(run, specs())

        self.assertTrue(planner.before_external_call())
        self.assertFalse(planner.before_external_call())
        self.assertEqual(planner.consumed, 1)
        self.assertEqual(planner.reserved, 1)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t27_budget_two_allows_two_units_and_blocks_third(self):
        run = run_stub(
            search_mode="profunda", budget_max_credits=2,
            budget_reserved_credits=2, budget_consumed_credits=0,
        )
        planner = planner_for_run(run, specs())

        self.assertEqual([planner.before_external_call() for _ in range(3)], [True, True, False])
        self.assertEqual(planner.stop_reason, "budget_exhausted")

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t28_call_accounting_preserves_full_hold_and_ledger_invariant(self):
        run = run_stub(
            search_mode="profunda", budget_max_credits=2,
            budget_reserved_credits=2, budget_consumed_credits=0,
        )
        planner = planner_for_run(run, specs())

        self.assertTrue(planner.before_external_call())
        self.assertEqual(run.budget_reserved_credits, 2)
        planner.apply_to_run(run, [])

        self.assertEqual(run.budget_reserved_credits, 2)
        self.assertLessEqual(run.budget_consumed_credits, run.budget_reserved_credits)
        self.assertLessEqual(run.budget_reserved_credits, run.budget_max_credits)
