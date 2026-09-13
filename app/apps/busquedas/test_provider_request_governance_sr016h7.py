"""Offline SR0.16H.7 request-governance contracts."""

from unittest import TestCase

from .services_hybrid_coverage_v261 import (
    _DETERMINISTIC_REQUEST_CAP_PER_PROVIDER,
    _DETERMINISTIC_TECHNICAL_FAILURE_THRESHOLD,
    _governance_can_request,
    _governance_eligible_locations,
    _governance_record,
    _new_deterministic_request_governance,
)


class ProviderRequestGovernanceSR016H7Tests(TestCase):
    def test_technical_zero_is_not_fallback_eligible(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "fotocasa", "Cartama", {
            "zero_reason": "RESULT_SCOPE_NOT_FOUND",
        })
        self.assertEqual(_governance_eligible_locations(governance, "fotocasa"), [])

    def test_scope_empty_without_marker_is_not_business_zero(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "habitaclia", "Málaga", {
            "zero_reason": "SCOPE_FOUND_NO_ANCHORS",
        })
        self.assertEqual(_governance_eligible_locations(governance, "habitaclia"), [])

    def test_confirmed_business_zero_is_fallback_eligible(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "fotocasa", "Málaga", {
            "zero_reason": "PROVIDER_EMPTY_CONFIRMED",
        })
        self.assertEqual(_governance_eligible_locations(governance, "fotocasa"), ["Málaga"])

    def test_nonzero_is_not_fallback_eligible(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "fotocasa", "Málaga", {"zero_reason": "NONZERO"})
        self.assertEqual(_governance_eligible_locations(governance, "fotocasa"), [])

    def test_circuit_breaker_opens_after_two_technical_failures(self):
        governance = _new_deterministic_request_governance()
        for location in ("A", "B"):
            _governance_record(governance, "fotocasa", location, {
                "zero_reason": "RESULT_SCOPE_NOT_FOUND",
            })
        self.assertEqual(governance["technical_failure_threshold"], 2)
        self.assertFalse(_governance_can_request(governance, "fotocasa"))

    def test_business_zero_does_not_open_circuit(self):
        governance = _new_deterministic_request_governance()
        _governance_record(governance, "fotocasa", "Málaga", {
            "zero_reason": "PROVIDER_EMPTY_CONFIRMED",
        })
        self.assertTrue(_governance_can_request(governance, "fotocasa"))

    def test_cap_includes_exact_and_fallback_requests(self):
        governance = _new_deterministic_request_governance()
        self.assertEqual(governance["provider_request_cap"], _DETERMINISTIC_REQUEST_CAP_PER_PROVIDER)
        for index in range(_DETERMINISTIC_REQUEST_CAP_PER_PROVIDER):
            state = governance["providers"].setdefault("habitaclia", {
                "provider_requests_attempted": 0,
                "provider_requests_succeeded": 0,
                "provider_requests_technical_failed": 0,
                "provider_requests_business_zero": 0,
                "provider_requests_skipped_circuit_breaker": 0,
                "consecutive_technical_failures": 0,
                "circuit_breaker_open": False,
                "circuit_breaker_reason": "",
                "records": [],
            })
            state["provider_requests_attempted"] = index + 1
            state["consecutive_technical_failures"] = 0
        self.assertFalse(_governance_can_request(governance, "habitaclia"))
