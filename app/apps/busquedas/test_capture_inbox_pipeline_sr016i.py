"""Offline contracts for SR0.16I review capture and replay preparation."""
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from .services_hybrid_coverage_v261 import _build_action_plan


class CaptureInboxPipelineSR016ITests(SimpleTestCase):
    def _review_row(self):
        return [{
            "source": "fotocasa",
            "candidates": [{
                "url": "https://www.fotocasa.es/es/flat/190475503",
                "title": "Piso de revisión",
                "price": 1000,
                "classification": "reviewable",
                "reason": "availability_unknown",
                "hard_violations": [],
            }],
        }]

    def test_review_candidate_is_capture_inbox_plan_and_not_actionable(self):
        with patch("apps.busquedas.services_hybrid_coverage_v261._existing_capture_id_for_url", return_value=None):
            plan = _build_action_plan(self._review_row())
        action = plan["actions"][0]
        self.assertEqual(action["action"], "would_create_in_review")
        self.assertEqual(action["target_status"], "in_review")
        self.assertFalse(action["actionability"]["actionable"])
        self.assertEqual(action["actionability"]["availability_state"], "UNKNOWN")
        self.assertEqual(plan["totals"]["would_create_in_review"], 1)

    def test_unavailable_is_not_capture_inbox_candidate(self):
        row = self._review_row()
        row[0]["candidates"][0].update({
            "classification": "discarded",
            "reason": "availability_unavailable:http_404",
        })
        with patch("apps.busquedas.services_hybrid_coverage_v261._existing_capture_id_for_url", return_value=None):
            plan = _build_action_plan(row)
        self.assertEqual(plan["actions"][0]["action"], "skip_discarded")
        self.assertEqual(plan["totals"].get("would_create_in_review", 0), 0)

    def test_replay_command_defaults_to_dry_run_without_provider_paths(self):
        fake_run = type("Run", (), {
            "pk": 220,
            "raw_response": {"source_coverage": self._review_row(), "context": {}},
            "search_profile": object(),
        })()
        manager = type("Manager", (), {"select_related": lambda self, *a: self,
                                        "get": lambda self, **kw: fake_run})()
        with patch("apps.busquedas.management.commands.replay_capture_inbox.SearchRun.objects", manager), \
             patch("apps.busquedas.services_hybrid_coverage_v261._existing_capture_id_for_url", return_value=None), \
             patch("apps.busquedas.management.commands.replay_capture_inbox._apply_action_plan_to_db") as apply:
            # Command output is the contract; dry-run must never invoke writer.
            call_command("replay_capture_inbox", run_id=220)
        apply.assert_not_called()
