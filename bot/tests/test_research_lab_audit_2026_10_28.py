# bot/tests/test_research_lab_audit_2026_10_28.py  # claude code changed: 2026-10-28 — new: Research Lab audit fixes
"""Regression tests for the 2026-10-28 Research Lab audit (owner: the cointegration capability "keeps asking for a
feature and the expected direction"). Real data, no mocking, per the repo convention."""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bot.quant_core.research_zones import ZONES
from bot.research_lab.capability_registry import RESEARCH_CAPABILITIES
from bot.research_lab.interpreter import describe_executed_spec, explain_evidence, suggest_spec
from bot.research_lab.models import ResearchExperiment, ResearchSubscription
from bot.research_lab.spec import ResearchSpec
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.research_tools import run_cointegration_test
from bot.research_lab.verdict import compute_verdict, compute_verdict_pairs


class PairsDetectionTest(SimpleTestCase):
    def test_pairs_phrasings_with_two_assets_become_pairs(self):
        for text, a, b in [
            ("EUR/USD and GBP/USD form a mean-reverting pair", "EUR/USD", "GBP/USD"),
            ("The spread between BTC and ETH mean-reverts", "BTC/USDT", "ETH/USDT"),
            ("Pairs trade AUD/USD vs NZD/USD", "AUD/USD", "NZD/USD"),
            ("co-integration between EUR/CHF and USD/CHF", "EUR/CHF", "USD/CHF"),
            ("ETH/BTC ratio is stationary", "ETH/USDT", "BTC/USDT"),
            ("BTC and ETH are cointegrated", "BTC/USDT", "ETH/USDT"),
        ]:
            s = suggest_spec(text)
            self.assertEqual((s.hypothesis_type, s.asset, s.asset_b), ("pairs", a, b), text)
            self.assertEqual(s.features, [], text)
            self.assertNotIn("direction", s.ambiguous_fields, text)   # a pairs spec never asks for a direction
            self.assertNotIn("target", s.ambiguous_fields, text)      # ... or a forward-return horizon

    def test_pairs_phrase_with_one_asset_is_not_pairs(self):
        s = suggest_spec("When the spread on BTC widens, returns mean-revert over 24 hours")
        self.assertNotEqual(s.hypothesis_type, "pairs")

    def test_feature_hypothesis_unchanged(self):
        s = suggest_spec("Higher RSI on BTC means higher 24-hour forward return")
        self.assertEqual((s.hypothesis_type, s.features, s.direction), ("feature", ["rsi"], "positive"))

    def test_pairs_description_names_no_feature_or_horizon(self):
        d = describe_executed_spec(ResearchSpec(hypothesis_text="x", asset="BTC/USDT", asset_b="ETH/USDT",
                                                timeframe="1h", hypothesis_type="pairs"))
        self.assertIn("BTC/USDT vs ETH/USDT", d)
        self.assertNotIn("no feature specified", d)
        self.assertNotIn("None candles", d)


class FormalizeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="audit1028", password="x")
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="ACTIVE", expires_at=None)
        self.client.login(username="audit1028", password="x")

    def test_pairs_form_needs_no_feature_direction_or_horizon(self):
        exp = ResearchExperiment.objects.create(student=self.user, hypothesis_text="Pairs trade BTC vs ETH")
        r = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id}), data={
            "hypothesis_type": "pairs", "asset": "BTC/USDT", "asset_b": "ETH/USDT", "timeframe": "1h"})
        self.assertEqual(r.status_code, 302)                     # straight to the plan — nothing else demanded
        exp.refresh_from_db()
        self.assertEqual(exp.structured_spec["hypothesis_type"], "pairs")

    def test_missing_hypothesis_type_is_an_error_not_a_silent_feature(self):
        exp = ResearchExperiment.objects.create(student=self.user, hypothesis_text="Pairs trade BTC vs ETH")
        r = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id}), data={
            "asset": "BTC/USDT", "asset_b": "ETH/USDT", "timeframe": "1h"})   # a disabled radio submits nothing
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "choose a hypothesis type")

    def test_formalize_get_preselects_pairs(self):
        exp = ResearchExperiment.objects.create(student=self.user, hypothesis_text="EUR/USD and GBP/USD form a mean-reverting pair")
        html = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id})).content.decode()
        self.assertRegex(html, r'value="pairs" id="type-pairs" checked')


class ZoneSDataTest(SimpleTestCase):
    def test_lab_data_stop_at_the_end_of_zone_s(self):
        for sym in ("BTC/USDT", "EUR/USD", "EUR/CAD"):
            df = load_ohlcv(sym)
            self.assertLessEqual(df.index[-1], ZONES.S1, sym)
            self.assertGreater(len(df.loc[ZONES.S0:]), 10_000, sym)
            self.assertEqual(df.attrs["data_window"]["zone"], "S")
            self.assertGreaterEqual(df.index[0], ZONES.init_window()[0], sym)   # only the D-8 warm-up window before S

    def test_feature_statistics_use_zone_s_rows_only(self):
        from bot.research_lab.tools.statistical_tools import _prepare
        enriched, _, _, _ = _prepare("EUR/USD", 24)
        ts = enriched["timestamp"] if "timestamp" in enriched.columns else enriched.index
        self.assertGreaterEqual(min(ts), ZONES.S0)


class BacktestDependenceTest(SimpleTestCase):
    def _bt(self, **kw):
        base = {"metric": "strategy_backtest", "n_trades_with_net_r": 400, "mean_net_r": 0.1,
                "p_value_one_sided": 0.01, "bootstrap_ci95_mean_net_r": [0.02, 0.18], "lag1_autocorrelation": 0.6}
        return {**base, **kw}

    def test_autocorrelated_trades_use_the_adjusted_p(self):
        from bot.research_lab.verdict import compute_verdict_backtest
        self.assertEqual(compute_verdict_backtest(self._bt(p_value_one_sided_ar1=0.2)).verdict, "INCONCLUSIVE")
        self.assertEqual(compute_verdict_backtest(self._bt(p_value_one_sided_ar1=None)).verdict, "INCONCLUSIVE")
        self.assertEqual(compute_verdict_backtest(self._bt(p_value_one_sided_ar1=0.01)).verdict, "OBSERVATION")


class CointegrationToolTest(SimpleTestCase):
    def test_crypto_pair_runs_on_zone_s(self):
        r = run_cointegration_test("BTC/USDT", "ETH/USDT")
        self.assertEqual(r["status"], "OK")
        self.assertGreater(r["n_zone_s_candles"], 20_000)
        self.assertIsNotNone(r["coint_pvalue"])

    def test_fx_holiday_quotes_are_dropped_and_disclosed(self):
        # majors quoted inside the signed holiday closures: those quotes are dropped (disclosed), not a crash
        r = run_cointegration_test("EUR/USD", "GBP/USD")
        self.assertNotEqual(r["status"], "BLOCKED")
        self.assertEqual(r["rows_dropped_inside_market_closures"]["EUR/USD"], 22)
        # the 7-hour New Year 2023 gap exceeds the signed 5-candle gap policy -> honest INSUFFICIENT_DATA
        self.assertIn("gap policy", r["reject_reason"])
        self.assertEqual(compute_verdict_pairs(r).verdict, "INSUFFICIENT_DATA")

    def test_fx_pair_without_gaps_runs(self):
        r = run_cointegration_test("NZD/USD", "EUR/CAD")
        self.assertEqual(r["status"], "OK")
        self.assertIsNotNone(r["coint_pvalue"])


class VerdictAndRegistryTest(SimpleTestCase):
    def test_missing_block_p_is_invalid_not_a_crash(self):
        v = compute_verdict({"sample_size": 5000, "ic": 0.05, "block_permutation_p_value": None}, None)
        self.assertEqual(v.verdict, "INVALID_RESEARCH")

    def test_family_of_one_is_not_called_a_correction(self):
        v = compute_verdict({"sample_size": 5000, "ic": 0.06, "block_permutation_p_value": 0.001},
                            {"passes_fdr": True}, hypothesized_direction="positive")
        text = " ".join(v.explanation)
        self.assertNotIn("survives FDR correction", text)
        self.assertIn("family of one", text)

    def test_cross_sectional_is_blocked_by_d6(self):
        cap = RESEARCH_CAPABILITIES["cross_sectional_research"]
        self.assertFalse(cap.operationally_ready)
        self.assertIn("D-6", cap.status_note)

    def test_pairs_evidence_is_explained_as_pairs(self):
        class E:   # the explain function only reads these three attributes
            verdict = "REJECTED"
            research_plan = {"spec": {"hypothesis_text": "x", "asset": "BTC/USDT", "asset_b": "ETH/USDT",
                                      "timeframe": "1h", "hypothesis_type": "pairs"}}
            statistical_results = {"is_cointegrated": False, "coint_pvalue": 0.5569, "hedge_ratio": 0.8875,
                                   "n_zone_s_candles": 21023, "status": "OK", "reject_reason": "Engle-Granger p=0.5569 >= 0.05"}
        text = explain_evidence(E())
        self.assertIn("Engle–Granger cointegration p-value", text)
        self.assertIn("not proof that none exists", text)
        self.assertNotIn("Information Coefficient", text)
