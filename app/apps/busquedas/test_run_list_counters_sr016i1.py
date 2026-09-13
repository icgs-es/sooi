"""SR0.16I.1 presentation counter contracts."""
from unittest.mock import patch

from django.test import SimpleTestCase

from .search_budget_ux import get_searchrun_list_metrics


class RunListCommercialCountersSR016I1Tests(SimpleTestCase):
    def _run(self, quality, *, action_totals=None, raw_candidates=None,
             total_found=0, total_new=0, total_updated=0, total_errors=0,
             pk=223):
        return type("Run", (), {
            "raw_response": {
                "quality_semantics": quality,
                "action_plan": {"totals": action_totals or {}},
                "total_candidates": raw_candidates or 0,
            },
            "pk": pk,
            "total_found": total_found,
            "total_new": total_new,
            "total_updated": total_updated,
            "total_errors": total_errors,
        })()

    def _captured_count(self, count):
        return patch(
            "apps.inmuebles.models.CapturedProperty.objects",
            type("Manager", (), {
                "filter": lambda _self, **kwargs: type(
                    "Query", (), {"count": lambda _self: count}
                )(),
            })(),
        )

    def test_run220_found_is_three_and_replay_created_three(self):
        run = self._run(
            {"raw_candidates": 16, "actionable_count": 0, "review_required_count": 3},
            action_totals={"skip_discarded": 13}, raw_candidates=16,
        )
        with self._captured_count(3):
            metrics = get_searchrun_list_metrics(run)
        self.assertEqual(metrics, {"found": 3, "new": 3, "updated": 0, "discarded": 13})

    def test_run221_found_is_five_and_duplicate_is_not_new(self):
        run = self._run(
            {"raw_candidates": 5, "actionable_count": 0, "review_required_count": 5},
            action_totals={"skip_discarded": 0}, raw_candidates=5,
        )
        # Four CapturedProperty rows are linked to Run221; one already-found
        # duplicate is intentionally not linked and is therefore not new.
        with self._captured_count(4):
            metrics = get_searchrun_list_metrics(run)
        self.assertEqual(metrics["found"], 5)
        self.assertEqual(metrics["new"], 4)
        self.assertEqual(metrics["updated"], 0)
        self.assertEqual(metrics["discarded"], 0)

    def test_review_and_actionable_are_not_double_counted(self):
        run = self._run({"raw_candidates": 8, "actionable_count": 3, "review_required_count": 5})
        with self._captured_count(0):
            self.assertEqual(get_searchrun_list_metrics(run)["found"], 8)

    def test_near_matches_are_not_exact_found(self):
        run = self._run({"raw_candidates": 2, "actionable_count": 0, "review_required_count": 0}, total_found=0)
        run.raw_response["recovery_observability"] = {"near_match_candidate_count": 2}
        with self._captured_count(0):
            metrics = get_searchrun_list_metrics(run)
        self.assertEqual(metrics["found"], 0)

    def test_legacy_counters_are_preserved_and_never_none(self):
        run = self._run({}, total_found=2, total_new=2, total_updated=0, total_errors=1, pk=None)
        with self._captured_count(0):
            metrics = get_searchrun_list_metrics(run)
        self.assertEqual(metrics, {"found": 2, "new": 2, "updated": 0, "discarded": 1})
        self.assertTrue(all(isinstance(value, int) and value >= 0 for value in metrics.values()))

    def test_new_counter_uses_search_run_fk_and_takes_precedence(self):
        run = self._run({}, total_new=99, pk=223)
        with self._captured_count(4):
            self.assertEqual(get_searchrun_list_metrics(run)["new"], 4)

    def test_zero_fk_with_positive_total_new_uses_legacy_fallback(self):
        run = self._run({}, total_new=4, pk=223)
        with self._captured_count(0):
            self.assertEqual(get_searchrun_list_metrics(run)["new"], 4)

    def test_capture_linked_to_other_run_is_not_counted(self):
        run = self._run({}, total_new=0, pk=223)
        seen = {}
        manager = type("Manager", (), {
            "filter": lambda _self, **kwargs: (seen.update(kwargs) or type(
                "Query", (), {"count": lambda _self: 0}
            )()),
        })()
        with patch("apps.inmuebles.models.CapturedProperty.objects", manager):
            self.assertEqual(get_searchrun_list_metrics(run)["new"], 0)
        self.assertEqual(seen, {"search_run_id": 223})

    def test_total_new_is_compatibility_fallback_when_fk_unavailable(self):
        run = self._run({}, total_new=4, pk=223)
        manager = type("Manager", (), {
            "filter": lambda _self, **kwargs: (_ for _ in ()).throw(RuntimeError("legacy relation unavailable")),
        })()
        with patch("apps.inmuebles.models.CapturedProperty.objects", manager):
            self.assertEqual(get_searchrun_list_metrics(run)["new"], 4)

    def test_no_capture_and_zero_total_new_returns_zero(self):
        run = self._run({}, total_new=0, pk=223)
        with self._captured_count(0):
            self.assertEqual(get_searchrun_list_metrics(run)["new"], 0)

    def test_run223_expected_counters_are_8_4_0_0(self):
        run = self._run(
            {"raw_candidates": 8, "actionable_count": 0, "review_required_count": 8},
            action_totals={"skip_discarded": 0}, raw_candidates=8, pk=223,
        )
        with self._captured_count(4):
            self.assertEqual(
                get_searchrun_list_metrics(run),
                {"found": 8, "new": 4, "updated": 0, "discarded": 0},
            )
