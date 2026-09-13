"""Offline SR0.16H.7.4 anchor-first and failure-class contracts."""

from unittest import TestCase

from .services_hybrid_coverage_v261 import (
    _governance_can_request,
    _governance_eligible_locations,
    _governance_record,
    _new_deterministic_request_governance,
    _provider_execution_locations,
)


class ProviderGovernanceAnchorSR016H74Tests(TestCase):
    def test_anchor_matches_normalized_province_and_executes_first(self):
        ctx = {"province": "Málaga", "search_locations": ["Cartama", "Málaga", "Churriana"]}
        self.assertEqual(_provider_execution_locations(ctx), ["Málaga", "Cartama", "Churriana"])

    def test_remaining_locations_preserve_relative_order(self):
        ctx = {"province": "Málaga", "search_locations": ["A", "Málaga", "B", "C"]}
        self.assertEqual(_provider_execution_locations(ctx)[1:], ["A", "B", "C"])

    def test_no_anchor_preserves_original_order(self):
        ctx = {"province": "Málaga", "search_locations": ["A", "B"]}
        self.assertEqual(_provider_execution_locations(ctx), ["A", "B"])

    def test_ambiguous_does_not_count_as_hard_technical_or_open_breaker(self):
        governance = _new_deterministic_request_governance()
        for location in ("A", "B", "C"):
            _governance_record(governance, "fotocasa", location, {
                "zero_reason": "RESULT_SCOPE_NOT_FOUND", "zero_class": "AMBIGUOUS",
            })
        state = governance["providers"]["fotocasa"]
        self.assertEqual(state["provider_requests_technical_failed"], 0)
        self.assertEqual(state["provider_requests_ambiguous"], 3)
        self.assertFalse(state["circuit_breaker_open"])
        self.assertTrue(_governance_can_request(governance, "fotocasa"))
        self.assertEqual(_governance_eligible_locations(governance, "fotocasa"), [])

    def test_two_hard_technical_failures_open_breaker(self):
        governance = _new_deterministic_request_governance()
        for location in ("Málaga", "Cartama"):
            _governance_record(governance, "habitaclia", location, {
                "zero_reason": "HTTP_NON_SUCCESS", "zero_class": "TECHNICAL",
            })
        state = governance["providers"]["habitaclia"]
        self.assertEqual(state["provider_requests_technical_failed"], 2)
        self.assertTrue(state["circuit_breaker_open"])
        self.assertFalse(_governance_can_request(governance, "habitaclia"))

    def test_business_zero_does_not_open_breaker_and_is_fallback_eligible(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "fotocasa", "Málaga", {
            "zero_reason": "PROVIDER_EMPTY_CONFIRMED", "zero_class": "BUSINESS",
        })
        state = governance["providers"]["fotocasa"]
        self.assertFalse(state["circuit_breaker_open"])
        self.assertEqual(_governance_eligible_locations(governance, "fotocasa"), ["Málaga"])

    def test_cap_remains_six_per_provider(self):
        governance = _new_deterministic_request_governance()
        self.assertEqual(governance["provider_request_cap"], 6)
