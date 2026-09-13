"""Focused offline contracts for SR0.15A; no network or provider calls."""

import json
from unittest import TestCase
from unittest.mock import patch

from .provider_query_provenance import executed_request, query_provenance, request_fingerprint
from .services_hybrid_coverage_v261 import (
    SOURCE_SPECS,
    SourceSpec,
    _candidate_constraint_violations,
    _deterministic_url,
    _refresh_provider_efficiency,
)
from .services_portal_extractors import fetch_html


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    status = 200
    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"<html></html>"

    def geturl(self):
        return "https://www.habitaclia.com/alquiler-cartama.htm?hab=3&pmax=1000"


class ProviderQueryProvenanceSR015ATests(TestCase):
    def test_habitaclia_planned_safe_query(self):
        url = _deterministic_url(
            SourceSpec("habitaclia", ("habitaclia.com",), "deterministic"),
            {"operation": "rent", "location": "Cártama", "bedrooms": 3, "max_price": 1000},
        )
        value = query_provenance("habitaclia", url, None, operation="rent", geography="Cártama")
        self.assertEqual(value["planned"]["safe_query"], {"hab": "3", "pmax": "1000"})
        self.assertEqual(value["executed"], "unknown")

    @patch("apps.busquedas.services_portal_extractors.urlopen", return_value=_Response())
    def test_habitaclia_executed_is_captured_at_network_boundary(self, mocked):
        url = "https://www.habitaclia.com/alquiler-cartama.htm?hab=3&pmax=1000"
        value = fetch_html(url, provider="habitaclia")["query_provenance"]
        self.assertEqual(value["executed"]["safe_query"], {"hab": "3", "pmax": "1000"})
        self.assertEqual(mocked.call_args.args[0].full_url, url)

    def test_planned_is_not_copied_to_executed(self):
        value = query_provenance(
            "habitaclia",
            "https://www.habitaclia.com/x?hab=3&pmax=1000",
            "https://www.habitaclia.com/x?hab=3",
        )
        self.assertEqual(value["planned"]["safe_query"]["pmax"], "1000")
        self.assertNotIn("pmax", value["executed"]["safe_query"])

    def test_fotocasa_real_min_rooms(self):
        value = executed_request("fotocasa", "https://www.fotocasa.es/x?minRooms=3")
        self.assertEqual(value["safe_query"], {"minRooms": "3"})

    def test_idealista_sdk_boundary_has_no_invented_response_location(self):
        value = query_provenance(
            "idealista", "", "https://api.openai.com/v1/responses",
            operation="web_search", geography="Cártama",
        )
        self.assertEqual(value["executed"]["host"], "api.openai.com")
        self.assertEqual(value["executed"]["safe_query"], {})
        self.assertEqual(value["response"]["status_class"], "unknown")
        self.assertEqual(value["response"]["final_host"], "")
        self.assertEqual(value["response"]["redirected"], "unknown")

    def test_secrets_and_unknown_parameters_are_redacted(self):
        url = (
            "https://www.habitaclia.com/x?hab=3&pmax=1000&token=x&api_key=x&"
            "session=x&cookie=x&authorization=x&csrf=x&password=x&email=x&foo=secret"
        )
        serialized = json.dumps(query_provenance("habitaclia", url, url)).lower()
        for forbidden in (
            "token", "api_key", "session", "cookie", "authorization", "csrf",
            "password", "email", "foo", "secret",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_request_fingerprint_is_stable_and_sensitive_to_safe_query(self):
        first = executed_request("habitaclia", "https://www.habitaclia.com/x?pmax=1000&hab=3")
        reordered = executed_request("habitaclia", "https://www.habitaclia.com/x?hab=3&pmax=1000")
        changed = executed_request("habitaclia", "https://www.habitaclia.com/x?hab=3&pmax=900")
        self.assertEqual(request_fingerprint(first), request_fingerprint(reordered))
        self.assertNotEqual(request_fingerprint(first), request_fingerprint(changed))

    def test_redirect_final_query_is_sanitized(self):
        value = query_provenance(
            "habitaclia", "https://www.habitaclia.com/x?pmax=1000",
            "https://www.habitaclia.com/x?pmax=1000", status=302,
            final_url="https://www.habitaclia.com/y?pmax=1000&token=secret&foo=secret",
        )
        self.assertTrue(value["response"]["redirected"])
        self.assertEqual(value["response"]["final_safe_query"], {"pmax": "1000"})
        self.assertNotIn("secret", json.dumps(value))

    def test_efficiency_metrics_are_observability_only(self):
        row = {"candidates": [
            {"classification": "discarded", "reason": "price_above_max:1001>1000", "price": 1001},
            {"classification": "reviewable", "reason": "availability_unknown:http_403", "hard_violations": []},
        ]}
        before = json.loads(json.dumps(row))
        _refresh_provider_efficiency(row, {"max_price": 1000})
        self.assertEqual(row["candidates"], before["candidates"])
        self.assertEqual(row["provider_efficiency"]["hard_rejected_count"], 1)
        self.assertEqual(row["provider_efficiency"]["review_required_count"], 1)

    def test_no_business_contract_change(self):
        self.assertEqual(
            [spec.slug for spec in SOURCE_SPECS[:3]],
            ["idealista", "fotocasa", "habitaclia"],
        )
        context = {"max_price": 1000, "bedrooms": 3}
        self.assertIn(
            "price_above_max:1001>1000",
            _candidate_constraint_violations(context, {"price": 1001}),
        )
        self.assertIn(
            "bedrooms_below_min:2<3",
            _candidate_constraint_violations(context, {"bedrooms": 2}),
        )
