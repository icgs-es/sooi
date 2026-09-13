"""Behavioral acceptance tests for the SR0.3B customer search-budget UX."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SearchProfile, SearchRun
from .commercial_metering import reserve_search_run
from .provider_gateway import ProviderCircuit, ProviderOutcome, ProviderResult
from .search_budget_ux import MODE_AUTHORIZATION, MODE_ORDER, MODE_UX, mode_previews, validate_authorization
from .search_reuse import write_analysis
from .searchrun_governance import initialize_search_run_governance
from .services_hybrid_coverage_v261 import SourceSpec, _ai_candidates_expanded


FLAG_ON = {"SOOI_SEARCH_GOVERNANCE_V1": "1"}
FLAG_OFF = {"SOOI_SEARCH_GOVERNANCE_V1": "0"}
PROVIDER = "apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search"
DISCOVERY = "apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates_expanded_v2615"
TASK = "apps.busquedas.views.run_search_profile_task.delay"


class ProviderBoundarySentinel(BaseException):
    """Escapes product exception handling while replacing the network boundary."""


class SearchBudgetUXSR03BTests(TestCase):
    def setUp(self):
        # Reuse assertions need a stable default regardless of the deployment
        # TTL.  Tests for other TTL values still override this per method.
        self.reuse_ttl = patch.dict(
            "os.environ", {"SOOI_SEARCH_REUSE_TTL_SECONDS": "3600"}, clear=False,
        )
        self.reuse_ttl.start()
        self.addCleanup(self.reuse_ttl.stop)
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="sr03b-owner", password="test")
        self.other = user_model.objects.create_user(username="sr03b-other", password="test")
        self.profile = SearchProfile.objects.create(
            owner=self.owner, name="Compra Sierra", province="Madrid", zone="Cercedilla",
            property_types=["house"], max_price=Decimal("350000"),
        )
        self.other_profile = SearchProfile.objects.create(
            owner=self.other, name="Perfil ajeno", province="Toledo", zone="Ocaña",
        )
        self.client.force_login(self.owner)

    def governed_run(self, *, profile=None, mode="eco", budget=None, **values):
        status = values.pop("status", SearchRun.Status.COMPLETED)
        asserted_values = dict(values)
        run = SearchRun.objects.create(
            search_profile=profile or self.profile,
            status=status,
            execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
            started_at=timezone.now(), finished_at=timezone.now(), **values,
        )
        maximum = MODE_AUTHORIZATION[mode]["maximum"] if budget is None else Decimal(str(budget))
        with patch.dict("os.environ", FLAG_ON, clear=False):
            initialize_search_run_governance(
                run, run.search_profile, search_mode=mode, budget_max_credits=maximum,
            )
        # Initialization deliberately zeros execution outcome counters. Populate
        # the completed-run evidence afterwards, as the real worker does.
        if asserted_values:
            for field, value in asserted_values.items():
                setattr(run, field, value)
            run.save(update_fields=[*asserted_values, "updated_at"])
        return run

    @patch.dict("os.environ", FLAG_OFF, clear=False)
    def test_t01_flag_off_preserves_legacy_search_ui(self):
        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ejecutar búsqueda")
        self.assertNotContains(response, "Autoriza la cobertura de esta ejecución")
        self.assertNotContains(response, "Profunda")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t02_flag_on_shows_four_governed_modes(self, _entitlement):
        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))
        self.assertEqual(response.status_code, 200)
        for label in ("Gratis", "Eco", "Amplia", "Profunda"):
            self.assertContains(response, label)
        self.assertContains(response, 'type="radio" name="search_mode"', count=4)

    def test_t03_free_copy_states_limited_coverage(self):
        preview = mode_previews(self.profile, self.owner)[0]
        self.assertEqual(preview["mode"], "free")
        self.assertIn("Cobertura limitada", preview["description"])
        self.assertEqual(preview["maximum_external_calls"], 0)

    def test_t04_profunda_copy_does_not_claim_unlimited_or_total_coverage(self):
        copy = MODE_UX["profunda"]["description"].casefold()
        self.assertIn("finita", copy)
        self.assertNotIn("ilimitad", copy)
        self.assertNotIn("todo el mercado", copy)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(DISCOVERY)
    @patch(PROVIDER)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t05_preflight_estimate_makes_no_provider_call(self, _allowed, provider, discovery):
        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Máximo:")
        provider.assert_not_called()
        discovery.assert_not_called()

    def test_t06_free_estimate_uses_zero_ai_calls(self):
        free = next(item for item in mode_previews(self.profile, self.owner) if item["mode"] == "free")
        self.assertFalse(free["external_ai_may_be_used"])
        self.assertEqual(free["maximum_external_calls"], 0)

    def test_t07_eco_estimate_is_bounded(self):
        eco = next(item for item in mode_previews(self.profile, self.owner) if item["mode"] == "eco")
        self.assertGreater(eco["maximum_external_calls"], 0)
        self.assertLessEqual(eco["maximum_external_calls"], MODE_AUTHORIZATION["eco"]["maximum"])

    def test_t08_profunda_estimate_is_finite(self):
        deep = next(item for item in mode_previews(self.profile, self.owner) if item["mode"] == "profunda")
        self.assertTrue(Decimal(deep["maximum_external_calls"]).is_finite())
        self.assertTrue(deep["maximum_authorization"].is_finite())

    def test_t09_invalid_mode_rejected_server_side(self):
        with self.assertRaises(ValidationError):
            validate_authorization(self.owner, "browser-invented", "0")

    def test_t10_negative_or_nonfinite_budget_rejected(self):
        for value in ("-1", "NaN", "Infinity", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_authorization(self.owner, "free", value)

    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t11_budget_above_mode_cap_rejected(self, _allowed):
        maximum = MODE_AUTHORIZATION["eco"]["maximum"]
        with self.assertRaises(ValidationError):
            validate_authorization(self.owner, "eco", maximum + 1)

    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(False, "Sin IA"))
    def test_t12_ai_mode_denied_when_entitlement_disallows_ai(self, entitlement):
        with self.assertRaises(ValidationError):
            validate_authorization(self.owner, "eco", "1")
        entitlement.assert_called_once_with(self.owner)

    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(False, "Sin IA"))
    def test_t13_free_mode_does_not_require_ai_entitlement(self, entitlement):
        self.assertEqual(validate_authorization(self.owner, "free", "0"), Decimal("0"))
        entitlement.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(DISCOVERY)
    @patch(PROVIDER)
    @patch(TASK)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t14_governed_launch_initializes_searchrun_authorization(self, _allowed, delay, provider, discovery):
        before = SearchRun.objects.count()
        response = self.client.post(reverse("searchprofile_execute", args=[self.profile.pk]), {
            "search_mode": "eco", "budget_max_credits": "3",
        })
        self.assertRedirects(response, reverse("searchprofile_detail", args=[self.profile.pk]))
        self.assertEqual(SearchRun.objects.count(), before + 1)
        run = SearchRun.objects.latest("pk")
        self.assertTrue(run.governance_enabled)
        self.assertEqual(run.search_mode, "eco")
        self.assertEqual(run.budget_max_credits, Decimal("3"))
        delay.assert_called_once_with(self.profile.pk, run.pk)
        provider.assert_not_called()
        discovery.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(PROVIDER)
    @patch(TASK)
    def test_t15_governed_launch_cannot_be_for_another_owner_profile(self, delay, provider):
        before = SearchRun.objects.count()
        response = self.client.post(reverse("searchprofile_execute", args=[self.other_profile.pk]), {
            "search_mode": "eco", "budget_max_credits": "1",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()
        provider.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch("apps.busquedas.services._run_hybrid_discovery_v2614")
    @patch(PROVIDER)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(False, "Sin autorización IA"))
    def test_t16_hybrid_include_ai_cannot_bypass_governance_entitlement(self, _allowed, provider, discovery):
        before = SearchRun.objects.count()
        response = self.client.post(reverse("searchprofile_hybrid_v261", args=[self.profile.pk]), {
            "action": "analyze", "include_ai": "1", "search_mode": "eco", "budget_max_credits": "1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SearchRun.objects.count(), before)
        provider.assert_not_called()
        discovery.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t17_run_detail_shows_mode_coverage_and_stop_reason(self):
        run = self.governed_run(mode="amplia", coverage_status="partial", stop_reason="completed_plan")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amplia")
        self.assertContains(response, "La cobertura ejecutada fue parcial")
        self.assertContains(response, "Se completó el plan técnico previsto")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t18_run_detail_shows_sources_executed_and_omitted(self):
        run = self.governed_run(sources_planned=11, sources_executed=7, sources_omitted=4)
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "11 planificadas · 7 ejecutadas · 4 omitidas")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t19_run_detail_shows_reuse_provenance(self):
        source = self.governed_run(cache_hits=0)
        run = self.governed_run(cache_hits=3, reused_from_search_run=source)
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sí (3 coincidencias)")
        self.assertContains(response, f"Ejecución #{source.pk}")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t20_no_results_copy_is_scoped_to_executed_coverage(self):
        run = self.governed_run(coverage_status="no_results")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se encontraron resultados dentro de la cobertura ejecutada")
        self.assertContains(response, "no prueba que el mercado esté vacío")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t21_limited_by_plan_copy_is_explicit(self):
        run = self.governed_run(coverage_status="limited_by_plan", stop_reason="plan_limit")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "limitada por el plan o modo autorizado")
        self.assertContains(response, "Se alcanzó el límite del plan autorizado")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t22_limited_by_budget_copy_is_explicit(self):
        run = self.governed_run(coverage_status="limited_by_budget", stop_reason="budget_exhausted")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "limitada por el máximo autorizado")
        self.assertContains(response, "Se alcanzó el máximo autorizado")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t23_degraded_provider_copy_is_customer_safe(self):
        run = self.governed_run(coverage_status="degraded_provider", stop_reason="provider_hard_failure")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "incidencia temporal del servicio")
        self.assertNotContains(response, "proveedor", status_code=200)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t24_internal_cost_fields_are_not_rendered(self):
        run = self.governed_run(estimated_internal_cost=Decimal("8765.4321"), actual_internal_cost=Decimal("7654.3210"))
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode().casefold()
        for sentinel in ("8765.4321", "7654.3210", "estimated_internal_cost", "actual_internal_cost"):
            self.assertNotIn(sentinel, body)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t25_customer_ui_does_not_render_openai_or_provider_account_errors(self):
        sentinel = "OPENAI_ACCOUNT_QUOTA_SECRET_91827"
        run = self.governed_run(
            coverage_status="failed", stop_reason="provider_hard_failure", provider="OpenAI",
            model_name="internal-model-secret", error_message=sentinel,
            raw_response={"provider_error": sentinel}, warnings=[sentinel],
        )
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode().casefold()
        for forbidden in (sentinel.casefold(), "openai", "internal-model-secret", "provider_error"):
            self.assertNotIn(forbidden, body)
        self.assertIn("no se muestran detalles internos", body)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    def test_t26_expand_action_not_available_from_profunda(self):
        run = self.governed_run(mode="profunda", coverage_status="partial")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Autorizar nueva búsqueda más amplia")

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(PROVIDER)
    @patch(TASK)
    def test_t27_expand_get_does_not_mutate_or_call_provider(self, delay, provider):
        run = self.governed_run(coverage_status="partial")
        before = SearchRun.objects.count()
        detail = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        get_expand = self.client.get(reverse("searchrun_expand", args=[run.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(get_expand.status_code, 405)
        self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()
        provider.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(TASK)
    def test_t28_expand_rejects_same_or_narrower_mode(self, delay):
        for target in ("free", "eco"):
            with self.subTest(target=target):
                run = self.governed_run(mode="eco", coverage_status="partial")
                before = SearchRun.objects.count()
                self.client.post(reverse("searchrun_expand", args=[run.pk]), {
                    "search_mode": target, "budget_max_credits": "0",
                })
                self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(TASK)
    def test_t29_expand_post_requires_owner(self, delay):
        run = self.governed_run(profile=self.other_profile, coverage_status="partial")
        before = SearchRun.objects.count()
        response = self.client.post(reverse("searchrun_expand", args=[run.pk]), {
            "search_mode": "amplia", "budget_max_credits": "1",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(TASK)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(False, "Sin IA"))
    def test_t30_expand_post_rechecks_entitlement(self, _allowed, delay):
        run = self.governed_run(mode="free", coverage_status="partial")
        before = SearchRun.objects.count()
        self.client.post(reverse("searchrun_expand", args=[run.pk]), {
            "search_mode": "eco", "budget_max_credits": "1",
        })
        self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(TASK)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t31_expand_creates_new_governed_run_with_broader_mode(self, _allowed, delay):
        old = self.governed_run(mode="eco", budget=3, coverage_status="partial", sources_executed=2)
        old_state = (old.search_mode, old.budget_max_credits, old.sources_executed)
        response = self.client.post(reverse("searchrun_expand", args=[old.pk]), {
            "search_mode": "amplia", "budget_max_credits": "6",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SearchRun.objects.filter(search_profile=self.profile).count(), 2)
        new = SearchRun.objects.exclude(pk=old.pk).get()
        old.refresh_from_db()
        self.assertEqual((old.search_mode, old.budget_max_credits, old.sources_executed), old_state)
        self.assertTrue(new.governance_enabled)
        self.assertEqual(new.search_mode, "amplia")
        self.assertEqual(new.budget_max_credits, Decimal("6"))
        delay.assert_called_once_with(self.profile.pk, new.pk)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(DISCOVERY)
    @patch(PROVIDER)
    @patch(TASK)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t32_expand_render_or_preflight_makes_zero_provider_calls(self, _allowed, delay, provider, discovery):
        run = self.governed_run(mode="eco", coverage_status="partial")
        response = self.client.get(reverse("searchrun_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ampliar búsqueda a Amplia")
        provider.assert_not_called()
        discovery.assert_not_called()
        delay.assert_not_called()

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch("apps.busquedas.services_hybrid_coverage_v261.run_hybrid_discovery_v261")
    @patch("apps.busquedas.services_hybrid_coverage_v261._apply_action_plan_to_db")
    def test_t33_write_after_analyze_still_has_zero_rediscovery(self, apply, discovery):
        apply.return_value = {"created": 0, "updated": 0, "skipped": 1, "created_ids": [], "updated_ids": [], "skipped_items": []}
        run = self.governed_run(raw_response={"context": {}, "source_coverage": []})
        before = run.calls_attempted
        write_analysis(run.pk, self.owner)
        discovery.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.calls_attempted, before)

    @patch.dict("os.environ", {**FLAG_ON, "SOOI_SEARCH_REUSE_TTL_SECONDS": "3600"}, clear=False)
    @patch(DISCOVERY)
    @patch(PROVIDER)
    def test_t34_fresh_reuse_still_has_zero_provider_calls(self, provider, discovery):
        old = self.governed_run(coverage_status=SearchRun.CoverageStatus.PARTIAL, raw_response={"context": {}, "source_coverage": [{
            "source": "cached", "attempted": True, "status": "success",
            "candidates": [
                {"url": f"https://example.invalid/{index}", "classification": "verified"}
                for index in range(3)
            ],
        }]})
        current = self.governed_run(status=SearchRun.Status.RUNNING)
        from .services import _run_hybrid_discovery_v2614
        _run_hybrid_discovery_v2614(self.profile, current, write_override=False)
        provider.assert_not_called()
        discovery.assert_not_called()
        current.refresh_from_db()
        self.assertEqual(current.cache_hits, 1)
        self.assertEqual(current.reused_from_search_run_id, old.pk)

    @patch(PROVIDER)
    def test_t35_hard_provider_failure_still_total_call_count_one(self, provider):
        provider.return_value = ProviderResult(ProviderOutcome.HARD_FAILURE, reason="insufficient_quota")
        circuit = ProviderCircuit("openai")
        context = {"location_scope": "multi_location", "search_locations": list("ABCDEFGHIJKLMNOP")}
        for index in range(8):
            _ai_candidates_expanded(SourceSpec(str(index), ("example.invalid",), "openai_web_search"), context, 8, circuit=circuit)
        self.assertEqual(provider.call_count, 1)

    def test_t36_no_duplicate_customer_credit_balance_is_created(self):
        busquedas_models = list(apps.get_app_config("busquedas").get_models())
        credit_account_models = [
            model for model in busquedas_models
            if {"owner", "user"} & {field.name for field in model._meta.fields}
            and any(token in model._meta.model_name for token in ("credit", "balance", "ledger", "wallet"))
        ]
        self.assertEqual(credit_account_models, [])
        tables = set(connection.introspection.table_names())
        self.assertFalse(any(
            table.startswith("busquedas_") and any(token in table for token in ("credit_account", "balance", "ledger", "wallet"))
            for table in tables
        ))
        self.assertEqual(
            {field.name for field in SearchRun._meta.fields if field.name.startswith("budget_")},
            {"budget_max_credits", "budget_reserved_credits", "budget_consumed_credits"},
        )

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_t37_customer_copy_does_not_promise_flat_ten_credit_cost_for_governed_variable_search(self, _allowed):
        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))
        body = response.content.decode().casefold()
        self.assertIn("máximo autorizado", body)
        self.assertNotIn("10 créditos por exploración ia", body)
        self.assertNotIn("requiere 10 créditos", body)

    def test_t38_no_city_or_portal_special_case_in_budget_ux_policy(self):
        source = Path(__file__).with_name("search_budget_ux.py").read_text(encoding="utf-8").casefold()
        self.assertIn("mode_policies", source)
        for forbidden in ("city", "portal", "municipality", "municipio", "madrid", "barcelona", "idealista"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(tuple(MODE_UX), MODE_ORDER)

    @patch.dict("os.environ", FLAG_ON, clear=False)
    @patch(PROVIDER, side_effect=ProviderBoundarySentinel)
    @patch(DISCOVERY, return_value=([], None))
    def test_t39_full_hold_customer_task_reaches_replaced_provider_boundary_without_network(
        self, discovery, provider,
    ):
        from .tasks import run_search_profile_task

        run = self.governed_run(
            mode="eco", budget=1, status=SearchRun.Status.PENDING,
        )
        reserve_search_run(run, self.owner)
        self.assertEqual(run.budget_reserved_credits, Decimal("1"))

        with self.assertRaises(ProviderBoundarySentinel):
            run_search_profile_task.run(self.profile.pk, run.pk)

        provider.assert_called_once()
        self.assertGreater(discovery.call_count, 0)
