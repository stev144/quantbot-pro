# bot/tests/test_research_lab_metric_words.py  # claude code changed: 2026-10-27 — new
"""Every Research Lab number has a plain-English meaning, on the report and the results page, for every result type
(owner: "a beginner doesn't necessarily know Mann-Whitney, FDR ... make sure that explanation applies to all our
research lab results")."""
import re
from html import unescape
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from bot.templatetags.research_extras import METRIC_WORDS, TOOL_WORDS, means, p_hint, tool_words

T = datetime(2026, 10, 26, 14, 54, tzinfo=timezone.utc)
TEMPLATES = ["research_lab/report.html", "research_lab/_metric_glossary.html"]

RESULTS = {
    "conditional": ({"hypothesis_type": "conditional", "target": {"horizon": 4}, "direction": "negative"},
                    {"metric": "conditional_event_test", "sample_size": 44007, "n_condition_true": 1164,
                     "n_condition_false": 42843, "n_condition_episodes": 500, "episode_ratio": 2.328, "horizon": 4,
                     "expected_direction": "negative", "observed_direction": "positive",
                     "mannwhitney_p_value": 2.67e-05, "block_permutation_p_value": 0.985,
                     "fdr_adjusted_p_value": None, "passes_fdr": None}),
    "pairs": ({"hypothesis_type": "pairs"}, {"coint_pvalue": 0.21, "adf_pvalue": 0.04, "hedge_ratio": 1.3,
                                             "half_life_time": 12, "half_life_time_unit": "hours"}),
    "backtest": ({"hypothesis_type": "strategy_backtest"},
                 {"metric": "strategy_backtest", "n_trades": 10, "n_trades_with_net_r": 10, "mean_net_r": -0.1,
                  "std_net_r": 1.0, "t_stat": -0.3, "p_value_one_sided": 0.6, "win_rate": 40.0, "profit_factor": 0.8,
                  "initial_balance": 1000.0, "final_balance": 900.0, "max_drawdown": 12.0, "max_drawdown_usd": 120.0}),
    "ic": ({"hypothesis_type": "feature"}, {"metric": "information_coefficient", "ic": 0.01,
                                            "block_permutation_p_value": 0.4, "sample_size": 5000}),
}


def _exp(kind):
    spec, stats = RESULTS[kind]
    return NS(id=uuid.uuid4(), hypothesis_text="h", research_plan={"spec": dict(spec, asset="BTC/USDT", timeframe="1h"),
                                                                   "data_availability": {"checks": [], "all_available": True}},
              statistical_results=stats, validation_results={}, created_at=T, started_at=T, completed_at=T,
              verdict="REJECTED", status="COMPLETED", warnings=[], random_seed=42, code_version="abc",
              tool_call_log=[{"tool_name": "inspect_dataset", "status": "success", "duration_seconds": 0.28, "error": ""},
                             {"tool_name": "run_conditional_test", "status": "success", "duration_seconds": 1.5, "error": ""}])


def _render(template, kind):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    return render_to_string(template, {"experiment": _exp(kind)}, request=request)


def _text(html):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))   # entities (&#x27;) as the reader sees them


class GlossaryCoverageTest(SimpleTestCase):
    def test_every_key_used_in_a_template_has_words(self):
        root = Path(settings.BASE_DIR) / "templates"
        for name in TEMPLATES:
            keys = re.findall(r'"([a-z_]+)"\|means', (root / name).read_text(encoding="utf-8"))
            self.assertTrue(keys, name)
            for key in keys:
                self.assertTrue(METRIC_WORDS.get(key), f"{name}: no words for {key!r}")

    def test_every_registered_tool_has_words(self):
        from bot.research_lab.policy_gate import ALLOWED_TOOLS
        self.assertEqual(set(ALLOWED_TOOLS) - set(TOOL_WORDS), set())
        self.assertEqual(tool_words("not_a_tool"), "not_a_tool")

    def test_p_hint(self):
        self.assertIn("unlikely to be chance", p_hint(0.01))
        self.assertIn("could easily be chance", p_hint(0.985))
        self.assertEqual(p_hint(None), "")


class EveryResultTypeTest(SimpleTestCase):
    def test_report_rows_all_have_a_meaning(self):
        for kind in RESULTS:
            html = _render("research_lab/report.html", kind)
            rows = re.findall(r'<tr><th scope="row">(.*?)</th>.*?<td>(.*?)</td></tr>', html, re.S)
            self.assertGreater(len(rows), 8, kind)
            empty = [_text(a).strip() for a, b in rows if not _text(b).strip()]
            self.assertEqual(empty, [], kind)
            self.assertIn("What it means", html, kind)

    def test_results_page_glossary_for_every_type(self):
        for kind in RESULTS:
            text = _text(_render("research_lab/_metric_glossary.html", kind))
            self.assertIn("What do these numbers mean?", text, kind)
            self.assertIn("A p-value is the chance", text, kind)

    def test_conflicting_tests_are_explained(self):
        text = _text(_render("research_lab/report.html", "conditional"))
        self.assertIn(means("mannwhitney")[:40], text)
        self.assertIn("trust this one", text)                 # MW < 0.05 but block permutation 0.985
        self.assertIn("Here they differ.", text)              # expected negative, observed positive
        self.assertIn("Here: the next 4 candles.", text)

    def test_engine_steps_are_in_plain_english_with_raw_log_kept(self):
        text = _text(_render("research_lab/report.html", "conditional"))
        self.assertIn("Engine steps — full tool call log (2 steps)", text)
        self.assertIn(tool_words("inspect_dataset"), text)
        self.assertIn("Raw log (for auditors)", text)
