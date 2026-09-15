import unittest
from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "seguimiento"
    / "opportunity_list.html"
).read_text(encoding="utf-8")


class MunicipalContextUIContractTests(unittest.TestCase):

    def test_endpoint(self):
        self.assertIn(
            "data-context-endpoint=",
            TEMPLATE,
        )

    def test_context_panel(self):
        self.assertIn(
            "SOOI_MUNICIPAL_CONTEXT_V1_UI",
            TEMPLATE,
        )
        self.assertIn(
            "Contexto municipal",
            TEMPLATE,
        )

    def test_metrics(self):
        for value in (
            "Habitantes",
            "Altitud",
            "Superficie",
            "Densidad",
        ):
            self.assertIn(value, TEMPLATE)

    def test_distance_disclosure(self):
        self.assertIn(
            "No equivale a distancia por carretera",
            TEMPLATE,
        )

    def test_async_contract(self):
        self.assertIn(
            'payload.contract !== "SOOI_MUNICIPAL_CONTEXT_V1"',
            TEMPLATE,
        )
        self.assertIn(
            "void loadMunicipalContext(feature)",
            TEMPLATE,
        )
