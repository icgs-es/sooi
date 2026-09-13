from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "templates/core/dashboard.html").read_text(encoding="utf-8")
CANONICAL_CSS = (ROOT / "apps/core/static/core/sooi_modern.css").resolve()
SHADOW_CSS = (ROOT / "static/core/sooi_modern.css").resolve()
BASE = (ROOT / "templates/core/base_private.html").read_text(encoding="utf-8")
SELECTOR = (ROOT / "apps/core/daily_workflow.py").read_text(encoding="utf-8")


def resolved_daily_css():
    resolved = finders.find("core/sooi_modern.css")
    if not resolved:
        raise AssertionError("Django staticfiles did not resolve core/sooi_modern.css")
    path = Path(resolved).resolve()
    return path, path.read_text(encoding="utf-8")


class DailyWorkflowSR021CPresentationTests(SimpleTestCase):
    def test_daily_counters_have_distinct_container(self):
        self.assertIn("data-daily-counters", TEMPLATE)
        self.assertEqual(TEMPLATE.count("data-daily-counter>"), 7)

    def test_daily_counters_not_rendered_as_single_inline_run(self):
        _, css = resolved_daily_css()
        self.assertIn(".daily-counter-grid {", css)
        self.assertIn("grid-template-columns: repeat(4", css)
        self.assertIn(".daily-counter strong,\n.daily-counter span {\n  display: block;", css)

    def test_attention_items_have_distinct_container(self):
        _, css = resolved_daily_css()
        self.assertIn("data-attention-item", TEMPLATE)
        self.assertIn(".daily-item {", css)

    def test_capture_items_have_distinct_container(self):
        _, css = resolved_daily_css()
        self.assertIn("data-capture-item", TEMPLATE)
        self.assertIn(".daily-compact-item {", css)

    def test_upcoming_action_items_have_distinct_container(self):
        self.assertEqual(TEMPLATE.count("data-upcoming-action-item"), 2)

    def test_each_attention_item_has_reason(self):
        attention_loop = TEMPLATE.split('{% for item in daily_workflow.attention %}', 1)[1].split('{% endfor %}', 1)[0]
        self.assertIn("{{ item.reason_label }}", attention_loop)

    def test_each_capture_item_has_direct_link(self):
        self.assertIn('href="/app/captacion/{{ item.object.pk }}/" class="daily-compact-item" data-capture-item', TEMPLATE)

    def test_each_opportunity_item_has_direct_link(self):
        self.assertIn('href="/app/oportunidades/{{ item.object.pk }}/" class="daily-compact-item"', TEMPLATE)

    def test_empty_upcoming_has_clear_empty_state(self):
        self.assertIn("No hay acciones programadas para los próximos 7 días.", TEMPLATE)

    def test_activity_today_has_separated_metrics(self):
        _, css = resolved_daily_css()
        self.assertEqual(TEMPLATE.count("data-activity-metric"), 4)
        self.assertIn(".daily-activity-grid {", css)

    def test_daily_styles_resolve_from_canonical_static_source(self):
        resolved, css = resolved_daily_css()
        self.assertEqual(resolved, CANONICAL_CSS)
        self.assertNotEqual(resolved, SHADOW_CSS)
        for selector in (
            ".daily-counter-grid",
            ".daily-counter",
            ".daily-item",
            ".daily-compact-item",
            ".daily-upcoming-list",
            ".daily-activity-grid",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_todo_al_dia_preserved(self):
        self.assertIn("Todo al día", TEMPLATE)
        self.assertIn("No hay acciones prioritarias pendientes para hoy.", TEMPLATE)

    def test_dashboard_label_hoy_preserved(self):
        self.assertIn("Hoy en SOOI", TEMPLATE)
        self.assertIn("¿Qué requiere tu atención?", TEMPLATE)

    def test_owner_scoping_preserved(self):
        self.assertIn("owner=user", SELECTOR)

    def test_no_business_rule_change(self):
        self.assertIn("sr021c-operational-ux", BASE)
        self.assertNotIn("sr021c", SELECTOR.lower())

    def test_no_literal_none(self):
        self.assertNotIn(">None<", TEMPLATE)
