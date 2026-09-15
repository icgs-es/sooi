import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent

VIEWS = (
    ROOT / "views.py"
).read_text(encoding="utf-8")

URLS = (
    ROOT / "urls.py"
).read_text(encoding="utf-8")


class MunicipalContextEndpointContractTests(
    unittest.TestCase
):

    def test_auth_and_get_only(self):
        marker = (
            "@login_required\n"
            "@require_GET\n"
            "def opportunity_map_municipal_context"
        )

        self.assertIn(
            marker,
            VIEWS,
        )

    def test_owner_and_identity_scope(self):
        self.assertIn(
            "owner=request.user,\n"
            "            geo_canonical_key=canonical_key,",
            VIEWS,
        )

    def test_discarded_excluded(self):
        self.assertIn(
            "PropertyOpportunity\n"
            "                .Status\n"
            "                .DISCARDED",
            VIEWS,
        )

    def test_registry_lookup_and_contract(self):
        self.assertIn(
            "get_municipal_context(",
            VIEWS,
        )

        self.assertIn(
            '"contract":\n'
            "                MUNICIPAL_CONTEXT_CONTRACT",
            VIEWS,
        )

    def test_route(self):
        self.assertIn(
            '"oportunidades/mapa/contexto/"',
            URLS,
        )

        self.assertIn(
            'name="opportunity_map_municipal_context"',
            URLS,
        )


if __name__ == "__main__":
    unittest.main()
