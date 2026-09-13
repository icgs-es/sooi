from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import SearchProfile, SearchRun
from .adaptive_planner import MODE_POLICIES
from .provider_gateway import ProviderCircuit, ProviderOutcome, ProviderResult
from .search_reuse import find_reusable_run, reusable_evidence, validate_analysis_run, write_analysis
from .searchrun_governance import initialize_search_run_governance
from .services_hybrid_coverage_v261 import SourceSpec, _ai_candidates_expanded


class ReuseAnalyzeWriteSR03A4Tests(TestCase):
    def setUp(self):
        # These tests exercise the documented default reuse window.  Keep them
        # independent from a deployment that deliberately disables reuse with
        # a zero-second TTL; individual expiry tests override this value.
        self.reuse_ttl = patch.dict(
            "os.environ", {"SOOI_SEARCH_REUSE_TTL_SECONDS": "3600"}, clear=False,
        )
        self.reuse_ttl.start()
        self.addCleanup(self.reuse_ttl.stop)
        User = get_user_model()
        self.owner = User.objects.create_user(username="a4-owner", password="x")
        self.other = User.objects.create_user(username="a4-other", password="x")
        self.profile = SearchProfile.objects.create(
            owner=self.owner, name="A4", province="Madrid", zone="Alcala",
            property_types=["flat"], max_price=200000,
        )

    def _run(self, *, profile=None, status=SearchRun.Status.COMPLETED,
             coverage=SearchRun.CoverageStatus.PARTIAL, age=timedelta(), payload=None):
        run = SearchRun.objects.create(
            search_profile=profile or self.profile, status=status,
            execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
            finished_at=timezone.now() - age,
        )
        initialize_search_run_governance(
            run, run.search_profile, search_mode="eco", budget_max_credits=20,
        )
        run.status = status
        run.coverage_status = coverage
        run.raw_response = payload if payload is not None else {
            "context": {},
            "source_coverage": [{
                "source": "generic", "attempted": True, "status": "success",
                "candidates": [{"url": "https://example.invalid/1", "classification": "verified"}],
            }],
        }
        run.save()
        return run

    @staticmethod
    def _accepted_rows(count=3, *, classification="verified", duplicate=False):
        return [{
            "source": "stored-source", "attempted": True, "status": "success",
            "candidates": [{
                "url": (
                    "https://example.invalid/same" if duplicate
                    else f"https://example.invalid/{index}"
                ),
                "classification": classification,
            } for index in range(count)],
        }]

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t01_t03_equivalent_owner_fingerprint_is_reusable(self):
        old = self._run()
        current = self._run(status=SearchRun.Status.RUNNING)
        self.assertEqual(find_reusable_run(current), old)
        current.search_fingerprint = "0" * 64
        self.assertIsNone(find_reusable_run(current))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t02_different_owner_is_never_reusable(self):
        self._run()
        other_profile = SearchProfile.objects.create(owner=self.other, name="Other", province="Madrid", zone="Alcala")
        current = self._run(profile=other_profile, status=SearchRun.Status.RUNNING)
        current.search_fingerprint = self.profile.runs.first().search_fingerprint
        current.save(update_fields=["search_fingerprint"])
        self.assertIsNone(find_reusable_run(current))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1", "SOOI_SEARCH_REUSE_TTL_SECONDS": "60"})
    def test_t04_stale_result_is_not_reusable(self):
        old = self._run(age=timedelta(minutes=2))
        SearchRun.objects.filter(pk=old.pk).update(finished_at=timezone.now() - timedelta(minutes=2))
        self.assertIsNone(find_reusable_run(self._run(status=SearchRun.Status.RUNNING)))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t05_t06_failed_and_degraded_empty_are_not_reused(self):
        self._run(status=SearchRun.Status.FAILED)
        self._run(coverage=SearchRun.CoverageStatus.DEGRADED_PROVIDER,
                  payload={"source_coverage": []})
        self.assertIsNone(find_reusable_run(self._run(status=SearchRun.Status.RUNNING)))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t07_reuse_updates_only_cache_and_provenance(self):
        old = self._run()
        current = self._run(status=SearchRun.Status.RUNNING)
        attempted = current.calls_attempted
        source, rows = reusable_evidence(current)
        self.assertEqual(source, old)
        self.assertTrue(rows[0]["reused"])
        self.assertEqual(current.cache_hits, 1)
        self.assertEqual(current.calls_attempted, attempted)
        self.assertEqual(current.reused_from_search_run_id, old.pk)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615")
    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t09_fresh_reuse_provider_call_count_zero(self, provider, deterministic):
        old = self._run(payload={"context": {}, "source_coverage": self._accepted_rows()})
        current = self._run(status=SearchRun.Status.RUNNING)
        calls_before = current.calls_attempted

        from .services import _run_hybrid_discovery_v2614
        _run_hybrid_discovery_v2614(self.profile, current, write_override=False)

        provider.assert_not_called()
        deterministic.assert_not_called()
        current.refresh_from_db()
        self.assertEqual(current.calls_attempted, calls_before)
        self.assertEqual(current.cache_hits, 1)
        self.assertEqual(current.reused_from_search_run_id, old.pk)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", [
        SourceSpec("later", ("example.invalid",), "deterministic"),
    ])
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615")
    def test_t10_sufficient_reuse_stops_before_later_discovery(self, discovery):
        self._run(payload={"context": {}, "source_coverage": self._accepted_rows()})
        current = self._run(status=SearchRun.Status.RUNNING)
        from .services_hybrid_coverage_v261 import run_hybrid_discovery_v261

        result = run_hybrid_discovery_v261(self.profile.pk, search_run=current)

        discovery.assert_not_called()
        self.assertEqual(current.stop_reason, SearchRun.StopReason.SUFFICIENT_COVERAGE)
        self.assertTrue(result["reused"])

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", [
        SourceSpec("later", ("example.invalid",), "deterministic"),
    ])
    @patch("apps.busquedas.services_hybrid_coverage_v261._is_applicable", return_value=True)
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615")
    def test_t11_insufficient_reuse_continues_to_later_stage(self, discovery, _applicable):
        discovery.return_value = ([], None)
        self._run(payload={"context": {}, "source_coverage": self._accepted_rows(1)})
        current = self._run(status=SearchRun.Status.RUNNING)
        from .services_hybrid_coverage_v261 import run_hybrid_discovery_v261

        run_hybrid_discovery_v261(self.profile.pk, search_run=current)

        discovery.assert_called_once()
        self.assertEqual(current.cache_hits, 1)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", [
        SourceSpec("later", ("example.invalid",), "deterministic"),
    ])
    @patch("apps.busquedas.services_hybrid_coverage_v261._is_applicable", return_value=True)
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615")
    def test_t12_discarded_reuse_does_not_satisfy_coverage(self, discovery, _applicable):
        discovery.return_value = ([], None)
        self._run(payload={
            "context": {},
            "source_coverage": self._accepted_rows(20, classification="discarded"),
        })
        current = self._run(status=SearchRun.Status.RUNNING)
        from .services_hybrid_coverage_v261 import run_hybrid_discovery_v261

        run_hybrid_discovery_v261(self.profile.pk, search_run=current)

        discovery.assert_called_once()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", [
        SourceSpec("later", ("example.invalid",), "deterministic"),
    ])
    @patch("apps.busquedas.services_hybrid_coverage_v261._is_applicable", return_value=True)
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615")
    def test_t12b_duplicate_reuse_does_not_satisfy_coverage(self, discovery, _applicable):
        discovery.return_value = ([], None)
        self._run(payload={
            "context": {}, "source_coverage": self._accepted_rows(20, duplicate=True),
        })
        current = self._run(status=SearchRun.Status.RUNNING)
        from .services_hybrid_coverage_v261 import run_hybrid_discovery_v261

        run_hybrid_discovery_v261(self.profile.pk, search_run=current)

        discovery.assert_called_once()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "0"})
    def test_t21_feature_flag_off_preserves_no_reuse(self):
        self._run()
        self.assertIsNone(find_reusable_run(self._run(status=SearchRun.Status.RUNNING)))

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t22_feature_flag_on_uses_governed_reuse(self):
        old = self._run()
        current = self._run(status=SearchRun.Status.RUNNING)
        self.assertTrue(current.governance_enabled)
        self.assertEqual(find_reusable_run(current), old)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t17_owner_validation(self):
        with self.assertRaises(PermissionDenied):
            validate_analysis_run(self._run().pk, self.other)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t18_fingerprint_validation(self):
        with self.assertRaises(ValidationError):
            validate_analysis_run(self._run().pk, self.owner, "f" * 64)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1", "SOOI_SEARCH_REUSE_TTL_SECONDS": "1"})
    def test_t19_expired_analysis_rejected(self):
        run = self._run()
        SearchRun.objects.filter(pk=run.pk).update(finished_at=timezone.now() - timedelta(seconds=2))
        with self.assertRaises(ValidationError):
            validate_analysis_run(run.pk, self.owner)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.run_hybrid_discovery_v261")
    def test_t20_governed_hybrid_path_cannot_bypass_searchrun(self, discovery):
        discovery.return_value = {
            "version": "V2.6.1.8-dry-run", "context": {}, "is_complete": True,
            "source_coverage": [], "action_plan": {"totals": {}},
            "write_result": None, "totals": {},
        }
        current = self._run(status=SearchRun.Status.RUNNING, payload={})
        from .services import _run_hybrid_discovery_v2614

        _run_hybrid_discovery_v2614(self.profile, current, write_override=False)

        self.assertEqual(discovery.call_count, 1)
        self.assertIs(discovery.call_args.kwargs["search_run"], current)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", [
        SourceSpec("paid", ("example.invalid",), "openai_web_search", tier="ai_efficient"),
    ])
    @patch("apps.busquedas.services_hybrid_coverage_v261._is_applicable", return_value=True)
    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t13_analyze_discovery_occurs_once(self, provider, _applicable):
        provider.return_value = ProviderResult(ProviderOutcome.SUCCESS, value="")
        run = self._run(status=SearchRun.Status.RUNNING, payload={})

        from .services import _run_hybrid_discovery_v2614
        _run_hybrid_discovery_v2614(self.profile, run, write_override=False, use_ai_override=True)

        self.assertEqual(provider.call_count, 1)
        run.refresh_from_db()
        self.assertEqual(run.calls_attempted, 1)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261.run_hybrid_discovery_v261")
    @patch("apps.busquedas.services_hybrid_coverage_v261._apply_action_plan_to_db")
    def test_t14_write_after_analyze_discovery_count_zero(self, apply, discovery):
        apply.return_value = {"created": 0, "updated": 0, "skipped": 1,
                              "created_ids": [], "updated_ids": [], "skipped_items": []}
        run = self._run()
        calls_before = run.calls_attempted
        write_analysis(run.pk, self.owner)
        discovery.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.calls_attempted, calls_before)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261._apply_action_plan_to_db")
    def test_t15_repeated_write_is_idempotent_and_uses_snapshot(self, apply):
        apply.return_value = {"created": 0, "updated": 0, "skipped": 1,
                              "created_ids": [], "updated_ids": [], "skipped_items": []}
        run = self._run()
        write_analysis(run.pk, self.owner)
        write_analysis(run.pk, self.owner)
        self.assertEqual(apply.call_count, 2)
        run.refresh_from_db()
        self.assertEqual(run.calls_attempted, 0)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.tasks.run_search_profile")
    def test_t23_celery_replay_of_completed_analysis_does_not_rediscover(self, discover):
        from .tasks import run_search_profile_task

        run = self._run()
        calls_before_replay = run.calls_attempted

        replayed_run_id = run_search_profile_task.run(self.profile.pk, run.pk)

        self.assertEqual(replayed_run_id, run.pk)
        discover.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.calls_attempted, calls_before_replay)

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t24_hard_provider_failure_total_provider_call_count_one(self, provider):
        provider.return_value = ProviderResult(
            ProviderOutcome.HARD_FAILURE, reason="insufficient_quota",
        )
        circuit = ProviderCircuit("openai")
        context = {
            "location_scope": "multi_location",
            "search_locations": list("ABCDEFGHIJKLMNOP"),
        }
        for index in range(8):
            _ai_candidates_expanded(
                SourceSpec(str(index), ("example.invalid",), "openai_web_search"),
                context, 8, circuit=circuit,
            )
        self.assertEqual(provider.call_count, 1)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    def test_t25_adaptive_call_caps_hold_with_reuse(self):
        self._run(payload={
            "context": {}, "source_coverage": self._accepted_rows(1),
        })
        current = self._run(status=SearchRun.Status.RUNNING)
        current.calls_attempted = MODE_POLICIES["eco"].max_ai_calls - 1
        current.calls_succeeded = current.calls_attempted
        current.budget_reserved_credits = Decimal(current.calls_attempted)
        current.budget_consumed_credits = Decimal(current.calls_attempted)
        current.save()
        source, rows = reusable_evidence(current)
        from .adaptive_planner import AdaptivePlanner
        planner = AdaptivePlanner(
            "eco", [SourceSpec("paid", ("example.invalid",), "openai_web_search")],
            budget_max=20, calls_attempted=current.calls_attempted,
            calls_succeeded=current.calls_succeeded,
            budget_reserved=current.budget_reserved_credits,
            budget_consumed=current.budget_consumed_credits,
        )
        accepted = sum(planner.before_external_call() for _ in range(5))
        planner.apply_to_run(current, rows)

        self.assertIsNotNone(source)
        self.assertEqual(accepted, 1)
        self.assertLessEqual(current.calls_attempted, MODE_POLICIES["eco"].max_ai_calls)
        self.assertLessEqual(current.calls_succeeded, current.calls_attempted)

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t26_reuse_validation_does_not_call_provider(self, provider):
        old = self._run()
        current = self._run(status=SearchRun.Status.RUNNING)
        self.assertEqual(find_reusable_run(current), old)
        validate_analysis_run(old.pk, self.owner)
        provider.assert_not_called()

    @patch.dict("os.environ", {"SOOI_SEARCH_GOVERNANCE_V1": "1"})
    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t27_reuse_provenance_is_inspectable(self, provider):
        old = self._run(payload={"context": {}, "source_coverage": self._accepted_rows()})
        current = self._run(status=SearchRun.Status.RUNNING)

        from .services import _run_hybrid_discovery_v2614
        _run_hybrid_discovery_v2614(self.profile, current, write_override=False)

        provider.assert_not_called()
        current.refresh_from_db()
        self.assertEqual(current.reused_from_search_run_id, old.pk)
        self.assertEqual(current.raw_response["reused_from_search_run_id"], old.pk)
        reused_rows = [row for row in current.raw_response["source_coverage"] if row.get("reused")]
        self.assertTrue(reused_rows)
        self.assertTrue(all(row["reused_from_search_run_id"] == old.pk for row in reused_rows))

    def test_t28_reuse_rules_contain_no_city_or_portal_special_case(self):
        from pathlib import Path
        text = Path(__file__).with_name("search_reuse.py").read_text()
        for forbidden in ("idealista", "madrid", "barcelona", "fotocasa"):
            self.assertNotIn(forbidden, text.casefold())
