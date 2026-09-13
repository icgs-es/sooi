"""Focused offline contracts for SR0.15C; no network/provider calls."""

from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from .adaptive_planner import AdaptivePlanner
from .commercial_policy import billable_consumption
from .provider_query_provenance import query_provenance
from .search_availability_semantics import classify_listing_availability
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import (
    SOURCE_SPECS,
    _candidate_constraint_violations,
    _classify_probe_outcome,
)
from .services_portal_extractors import extract_habitaclia_candidates


LISTING_URL = "https://www.habitaclia.com/alquiler-piso-cartama-i123456.htm"


def classify(status, html="", final_url=LISTING_URL, **extra):
    return classify_listing_availability(
        requested_url=LISTING_URL,
        status_code=status,
        final_url=final_url,
        html=html,
        **extra,
    )


def strong_listing_html():
    return """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "RealEstateListing",
      "url": "https://www.habitaclia.com/alquiler-piso-cartama-i123456.htm",
      "offers": {"@type": "Offer", "availability": "https://schema.org/InStock"}
    }
    </script></head><body>Ficha del inmueble</body></html>
    """


class StrongAvailabilityEvidenceSR015CTests(TestCase):
    def test_http_404_is_unavailable(self):
        self.assertEqual(classify(404)["state"], "unavailable")

    def test_http_410_is_unavailable(self):
        self.assertEqual(classify(410)["state"], "unavailable")

    def test_http_403_is_unknown(self):
        self.assertEqual(classify(403)["state"], "unknown")

    def test_http_200_generic_html_is_unknown(self):
        self.assertEqual(classify(200, "<html>" + ("contenido " * 1000) + "</html>")["state"], "unknown")

    def test_http_200_listing_strong_positive_is_confirmed(self):
        evidence = classify(200, strong_listing_html())
        self.assertEqual(evidence, {
            "state": "confirmed",
            "signal": "structured_listing_in_stock_same_listing",
            "source": "structured_data",
        })

    def test_http_302_to_home_is_unknown(self):
        self.assertEqual(classify(302, "<html>portal</html>", "https://www.habitaclia.com/")["state"], "unknown")

    def test_http_302_to_search_is_unknown(self):
        final = "https://www.habitaclia.com/buscar?text=cartama"
        self.assertEqual(classify(302, "<html>resultados</html>", final)["state"], "unknown")

    def test_explicit_removed_text_is_unavailable(self):
        evidence = classify(
            200, "<html>lo sentimos, este anuncio ya no está publicado</html>",
            explicit_negative="ya no está publicado",
        )
        self.assertEqual(evidence["state"], "unavailable")
        self.assertEqual(evidence["source"], "explicit_text")

    def test_absence_of_removed_text_is_not_confirmed(self):
        self.assertNotEqual(classify(200, "<html>página normal</html>")["state"], "confirmed")

    def test_body_length_is_not_confirmation(self):
        evidence = classify(200, "x" * 500_000)
        self.assertEqual(evidence["state"], "unknown")

    def test_search_intent_is_not_availability_evidence(self):
        html = "<html>Búsqueda solicitada: Cártama, piso, 3 habitaciones</html>"
        self.assertEqual(classify(200, html)["state"], "unknown")

    def test_query_provenance_is_not_availability_evidence(self):
        provenance = query_provenance("habitaclia", LISTING_URL + "?hab=3&pmax=1000", LISTING_URL)
        self.assertTrue(provenance["executed"]["request_fingerprint"])
        self.assertEqual(classify(200, str(provenance))["state"], "unknown")

    def test_hard_filters_unchanged(self):
        ctx = {"max_price": 1000, "bedrooms": 3}
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(ctx, {"price": 1001}))
        self.assertIn("bedrooms_below_min:2<3", _candidate_constraint_violations(ctx, {"bedrooms": 2}))

    def test_sr014e_actionability_contract(self):
        unknown = result_actionability({
            "classification": "verified", "reason": "deterministic_candidate_probe_available",
            "availability_evidence": {"state": "unknown", "signal": "no_strong_positive_signal"},
        })
        confirmed = result_actionability({
            "classification": "verified", "reason": "deterministic_candidate_probe_available",
            "availability_evidence": {"state": "confirmed", "signal": "structured_listing_in_stock_same_listing"},
        })
        self.assertFalse(unknown["actionable"])
        self.assertTrue(unknown["review_required"])
        self.assertTrue(confirmed["actionable"])

    def test_legacy_actionability_remains_compatible(self):
        legacy = result_actionability({
            "classification": "verified", "reason": "deterministic_candidate_probe_available",
        })
        self.assertTrue(legacy["actionable"])

    def test_new_probe_evidence_controls_classification_projection(self):
        self.assertEqual(_classify_probe_outcome({
            "availability_evidence": classify(200, strong_listing_html()),
        })[0], "MATCH")
        self.assertEqual(_classify_probe_outcome({
            "availability_evidence": classify(200, "<html>generic</html>"),
        })[0], "UNKNOWN")

    def test_sr015a_provenance_unchanged(self):
        value = query_provenance("habitaclia", LISTING_URL + "?hab=3&pmax=1000", LISTING_URL + "?hab=3&pmax=1000")
        self.assertEqual(value["executed"]["safe_query"], {"hab": "3", "pmax": "1000"})

    def test_sr015b_card_precision_unchanged(self):
        html = (
            '<article><a href="/alquiler-piso-cartama-i111.htm">Piso en Cártama</a><span>900 €</span></article>'
            '<article><a href="/alquiler-casa-pizarra-i222.htm">Casa en Pizarra</a><span>1.400 €</span></article>'
        )
        rows = {row["source_url"]: row for row in extract_habitaclia_candidates(html, LISTING_URL)["candidates"]}
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-piso-cartama-i111.htm"]["price"], 900)
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-casa-pizarra-i222.htm"]["price"], 1400)

    def test_provider_order_unchanged(self):
        self.assertEqual([spec.slug for spec in SOURCE_SPECS[:3]], ["idealista", "fotocasa", "habitaclia"])

    def test_budget_and_metering_unchanged(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        self.assertTrue(planner.before_external_call())
        self.assertFalse(planner.before_external_call())
        self.assertEqual(planner.consumed, Decimal("1"))
        self.assertEqual(
            billable_consumption(SimpleNamespace(budget_consumed_credits=Decimal("1"))),
            Decimal("1"),
        )
