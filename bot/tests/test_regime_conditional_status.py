# ============================================================
# bot/tests/test_regime_conditional_status.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 18/19. Exercises every branch of the decision tree using
# real RegimeConditionalICReport objects (from compute_regime_conditional_ic
# on synthetic planted-signal data) plus hand-built interaction/OOS/
# permutation evidence dicts — never mocked statistics, only mocked
# "was this later stage run at all."
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.regime_conditional_ic import compute_regime_conditional_ic, test_regime_interaction
from bot.research.regime_conditional_status import compute_regime_conditional_status


def _planted_panel(n=2000, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    regime = np.where(np.arange(n) % 2 == 0, "TRENDING_UP", "RANGING")
    feature = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    forward_return = np.where(regime == "TRENDING_UP", 0.05 * feature + 0.01 * noise, 0.01 * noise)
    return pd.DataFrame({"feature": feature, "forward_return": forward_return, "trend_state": regime}, index=idx)


def _no_signal_panel(n=2000, seed=99):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    regime = np.where(np.arange(n) % 2 == 0, "TRENDING_UP", "RANGING")
    feature = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    return pd.DataFrame({"feature": feature, "forward_return": noise, "trend_state": regime}, index=idx)


class RegimeConditionalStatusTest(SimpleTestCase):
    def test_no_evidence_when_nothing_survives_fdr(self):
        df = _no_signal_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        result = compute_regime_conditional_status("TRENDING_UP", ic_report)
        self.assertEqual(result.status, "NO_EVIDENCE")

    def test_insufficient_sample_for_a_tiny_regime(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=100000)
        result = compute_regime_conditional_status("TRENDING_UP", ic_report)
        self.assertEqual(result.status, "INSUFFICIENT_SAMPLE")

    def test_insufficient_data_for_a_never_observed_regime(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        result = compute_regime_conditional_status("HIGH_VOLATILITY_REGIME_NEVER_SEEN", ic_report)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")

    def test_statistically_detectable_without_interaction_evidence(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        result = compute_regime_conditional_status("TRENDING_UP", ic_report, interaction_result=None)
        self.assertEqual(result.status, "STATISTICALLY_DETECTABLE")

    def test_stays_statistically_detectable_when_interaction_not_significant(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        fake_interaction = {"status": "OK", "interaction_significant": False, "p_value": 0.8}
        result = compute_regime_conditional_status("TRENDING_UP", ic_report, interaction_result=fake_interaction)
        self.assertEqual(result.status, "STATISTICALLY_DETECTABLE")

    def test_regime_dependent_with_real_significant_interaction(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")
        self.assertTrue(interaction["interaction_significant"])   # sanity: the plant actually worked
        result = compute_regime_conditional_status("TRENDING_UP", ic_report, interaction_result=interaction)
        self.assertEqual(result.status, "REGIME_DEPENDENT")

    def test_oos_supported_when_regime_dependent_and_positive_net_return(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")

        class _FakeOOS:
            pooled_by_regime = {"TRENDING_UP": {"sufficient_sample": True, "metrics": {"mean_net_return": 0.002}}}

        result = compute_regime_conditional_status("TRENDING_UP", ic_report, interaction_result=interaction, oos_by_regime=_FakeOOS())
        self.assertEqual(result.status, "OOS_SUPPORTED")

    def test_research_negative_when_oos_net_return_is_not_positive(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")

        class _FakeOOS:
            pooled_by_regime = {"TRENDING_UP": {"sufficient_sample": True, "metrics": {"mean_net_return": -0.001}}}

        result = compute_regime_conditional_status("TRENDING_UP", ic_report, interaction_result=interaction, oos_by_regime=_FakeOOS())
        self.assertEqual(result.status, "RESEARCH_NEGATIVE")

    def test_permutation_supported_chain(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")

        class _FakeOOS:
            pooled_by_regime = {"TRENDING_UP": {"sufficient_sample": True, "metrics": {"mean_net_return": 0.002}}}

        permutation_verdict = {"status": "OK", "edge_appears_real": True}
        result = compute_regime_conditional_status(
            "TRENDING_UP", ic_report, interaction_result=interaction, oos_by_regime=_FakeOOS(),
            permutation_verdict_for_regime=permutation_verdict,
        )
        self.assertEqual(result.status, "ECONOMICALLY_SUPPORTED")

    def test_demoted_to_research_negative_when_permutation_fails(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")

        class _FakeOOS:
            pooled_by_regime = {"TRENDING_UP": {"sufficient_sample": True, "metrics": {"mean_net_return": 0.002}}}

        permutation_verdict = {"status": "OK", "edge_appears_real": False}
        result = compute_regime_conditional_status(
            "TRENDING_UP", ic_report, interaction_result=interaction, oos_by_regime=_FakeOOS(),
            permutation_verdict_for_regime=permutation_verdict,
        )
        self.assertEqual(result.status, "RESEARCH_NEGATIVE")

    def test_economically_supported_requires_net_return_above_threshold(self):
        df = _planted_panel()
        ic_report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        interaction = test_regime_interaction(df, "feature", "forward_return", "trend_state")

        class _FakeOOS:
            pooled_by_regime = {"TRENDING_UP": {"sufficient_sample": True, "metrics": {"mean_net_return": 0.0000001}}}

        permutation_verdict = {"status": "OK", "edge_appears_real": True}
        result = compute_regime_conditional_status(
            "TRENDING_UP", ic_report, interaction_result=interaction, oos_by_regime=_FakeOOS(),
            permutation_verdict_for_regime=permutation_verdict, min_economic_net_return_bps=1.0,
        )
        self.assertEqual(result.status, "RESEARCH_NEGATIVE")

    def test_never_promotes_past_statistically_detectable_without_being_asked_for_a_specific_regime(self):
        # claude code changed: structural anti-cherry-picking check — the
        # function signature itself REQUIRES regime_of_interest; there is
        # no code path that scans all regimes and returns the best one.
        import inspect
        sig = inspect.signature(compute_regime_conditional_status)
        self.assertIn("regime_of_interest", sig.parameters)
        self.assertEqual(sig.parameters["regime_of_interest"].default, inspect.Parameter.empty)
