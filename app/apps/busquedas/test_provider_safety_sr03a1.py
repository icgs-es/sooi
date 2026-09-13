from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from apps.busquedas.provider_gateway import (
    ProviderCircuit,
    ProviderCircuitState,
    ProviderOutcome,
    ProviderResult,
    call_provider,
    classify_provider_exception,
)
from apps.busquedas.services_hybrid_coverage_v261 import (
    SourceSpec,
    _ai_candidates_expanded,
    _call_openai_web_search,
    run_hybrid_discovery_v261,
)


class ProviderError(Exception):
    def __init__(self, message, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ProviderSafetyUnitTests(TestCase):
    def test_t01_hard_quota_opens_circuit(self):
        circuit = ProviderCircuit("test")
        result = call_provider(
            circuit, lambda: (_ for _ in ()).throw(ProviderError("insufficient_quota"))
        )
        self.assertEqual(result.outcome, ProviderOutcome.HARD_FAILURE)
        self.assertEqual(circuit.state, ProviderCircuitState.OPEN)

    def test_t02_credit_balance_exhausted_is_hard(self):
        self.assertEqual(
            classify_provider_exception(ProviderError("credit_balance_exhausted")),
            ProviderOutcome.HARD_FAILURE,
        )

    def test_t03_auth_failures_are_hard(self):
        for error in (ProviderError("invalid_api_key"), ProviderError("denied", 401)):
            with self.subTest(error=error):
                self.assertEqual(classify_provider_exception(error), ProviderOutcome.HARD_FAILURE)

    def test_t04_transient_failures_are_retryable(self):
        errors = [
            TimeoutError("timed out"), ConnectionResetError("connection reset"),
            ProviderError("temporary network error"), ProviderError("server", 503),
            ProviderError("true temporal rate limit", 429),
        ]
        for error in errors:
            with self.subTest(error=error):
                self.assertEqual(
                    classify_provider_exception(error), ProviderOutcome.RETRYABLE_FAILURE
                )

    def test_t05_hard_failure_does_not_attempt_alternate_tool(self):
        create = Mock(side_effect=ProviderError("credit_balance_exhausted", 429))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        openai_module = SimpleNamespace(OpenAI=Mock(return_value=client))
        with patch.dict("sys.modules", {"openai": openai_module}), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False
        ):
            result = _call_openai_web_search("prompt", ProviderCircuit("openai"))
        self.assertEqual(result.outcome, ProviderOutcome.HARD_FAILURE)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs["tools"], [{"type": "web_search"}])

    def test_t06_open_circuit_skips_without_invocation(self):
        operation = Mock()
        circuit = ProviderCircuit("test")
        circuit.open("quota")
        result = call_provider(circuit, operation)
        self.assertEqual(result.outcome, ProviderOutcome.SKIPPED_CIRCUIT)
        operation.assert_not_called()

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_t07_remaining_municipality_batches_skipped(self, provider):
        provider.return_value = ProviderResult(
            ProviderOutcome.HARD_FAILURE, reason="insufficient_quota"
        )
        circuit = ProviderCircuit("openai")
        spec = SourceSpec("ai", ("example.test",), "openai_web_search")
        ctx = {"location_scope": "multi_location", "search_locations": list("ABCDEFGH")}
        _ai_candidates_expanded(spec, ctx, 8, circuit=circuit)
        self.assertEqual(provider.call_count, 1)


class ProviderSafetyIntegrationTests(TestCase):
    def _run_matrix(self):
        specs = [SourceSpec(f"ai-{i}", (f"{i}.test",), "openai_web_search") for i in range(8)]
        profile_model = Mock()
        profile_model.objects.get.return_value = SimpleNamespace(id=7)
        ctx = {
            "location_scope": "multi_location", "search_locations": list("ABCDEFGHIJKLMNOP"),
            "property_types": [], "operation": "sale", "is_land": False,
        }

        def hard_failure(_prompt, **_kwargs):
            return ProviderResult(ProviderOutcome.HARD_FAILURE, reason="insufficient_quota")

        patches = [
            patch("apps.busquedas.services_hybrid_coverage_v261.SOURCE_SPECS", specs),
            patch("apps.busquedas.services_hybrid_coverage_v261.apps.get_model", return_value=profile_model),
            patch("apps.busquedas.services_hybrid_coverage_v261._profile_context", return_value=dict(ctx)),
            patch("apps.busquedas.services_hybrid_coverage_v261._profile_property_types_list", return_value=[]),
            patch("apps.busquedas.services_hybrid_coverage_v261._terrenos_es_applicable", return_value=True),
            patch("apps.busquedas.services_hybrid_coverage_v261._apply_auto_location"),
            patch("apps.busquedas.services_hybrid_coverage_v261._apply_search_locations_v261"),
            patch("apps.busquedas.services_hybrid_coverage_v261._sr0_repair_search_locations"),
            patch("apps.busquedas.services_hybrid_coverage_v261._is_applicable", return_value=True),
            patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search", side_effect=hard_failure),
        ]
        entered = [item.start() for item in patches]
        try:
            result = run_hybrid_discovery_v261(7, use_ai=True)
            provider = entered[-1]
        finally:
            for item in reversed(patches):
                item.stop()
        return result, provider

    def test_t08_later_ai_sources_are_skipped(self):
        result, _provider = self._run_matrix()
        self.assertEqual(result["source_coverage"][0]["provider_outcome"], "HARD_FAILURE")
        self.assertTrue(all(r["status"] == "skipped_circuit" for r in result["source_coverage"][1:]))

    def test_t09_four_batch_by_eight_source_matrix_invokes_provider_once(self):
        _result, provider = self._run_matrix()
        self.assertEqual(provider.call_count, 1)

    def test_t10_coverage_explains_circuit_skips(self):
        result, _provider = self._run_matrix()
        skipped = result["source_coverage"][1]
        self.assertEqual(skipped["provider_outcome"], "SKIPPED_CIRCUIT")
        self.assertEqual(skipped["error"], "provider_circuit_open")
        self.assertIn("insufficient_quota", skipped["provider_reason"])
        self.assertEqual(result["totals"]["skipped_circuit"], 7)

    def test_t11_provider_exception_is_governed_not_raised(self):
        circuit = ProviderCircuit("test")
        result = call_provider(
            circuit, lambda: (_ for _ in ()).throw(ProviderError("account disabled"))
        )
        self.assertEqual(result.outcome, ProviderOutcome.HARD_FAILURE)
