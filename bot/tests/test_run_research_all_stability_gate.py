# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-4: feature_stability_analyzer.py
# existed but nothing in run_research_all.py's orchestration ever called
# it, so a feature could reach KEEP/STRONG KEEP from significance testing
# alone without its temporal stability ever being checked). Tests the
# downgrade-only gate applied after family-wide correction.

import pandas as pd
from django.test import SimpleTestCase

from bot.run_research_all import _apply_stability_gate


def _corrected_row(feature, recommendation, confidence_level=95.0):
    return {
        "feature": feature,
        "recommendation": recommendation,
        "confidence_level": confidence_level,
        "ic_overall": 0.1,
    }


def _stability_row(feature, trend, live_recommendation):
    return {"symbol": "TEST_USDT", "feature": feature, "trend": trend, "live_recommendation": live_recommendation}


class StabilityGateTest(SimpleTestCase):

    def test_downgrades_strong_keep_when_no_stability_evidence(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "STRONG KEEP")])
        empty_stability = pd.DataFrame()

        gated = _apply_stability_gate(corrected, empty_stability)

        row = gated.iloc[0]
        self.assertEqual(row["recommendation"], "REVIEW")
        self.assertEqual(row["confidence_level"], 50.0)
        self.assertFalse(row["stability_gate_passed"])
        self.assertIn("no stability evidence", row["stability_gate_reason"])

    def test_downgrades_when_trend_is_decaying_even_with_good_live_recommendation(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="DECAYING", live_recommendation="TRADE NOW")])

        gated = _apply_stability_gate(corrected, stability)

        row = gated.iloc[0]
        self.assertEqual(row["recommendation"], "REVIEW")
        self.assertFalse(row["stability_gate_passed"])
        self.assertIn("DECAYING", row["stability_gate_reason"])

    def test_downgrades_when_dead(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "STRONG KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="DEAD", live_recommendation="WATCH")])

        gated = _apply_stability_gate(corrected, stability)

        self.assertEqual(gated.iloc[0]["recommendation"], "REVIEW")

    def test_downgrades_when_live_recommendation_is_avoid(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="STABLE", live_recommendation="AVOID")])

        gated = _apply_stability_gate(corrected, stability)

        row = gated.iloc[0]
        self.assertEqual(row["recommendation"], "REVIEW")
        self.assertIn("AVOID", row["stability_gate_reason"])

    def test_downgrades_when_live_recommendation_is_research(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="STABLE", live_recommendation="RESEARCH")])

        gated = _apply_stability_gate(corrected, stability)

        self.assertEqual(gated.iloc[0]["recommendation"], "REVIEW")

    def test_passes_when_trend_stable_and_recommendation_trade_now(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "STRONG KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="STABLE", live_recommendation="TRADE NOW")])

        gated = _apply_stability_gate(corrected, stability)

        row = gated.iloc[0]
        self.assertEqual(row["recommendation"], "STRONG KEEP")
        self.assertTrue(row["stability_gate_passed"])
        self.assertEqual(row["stability_gate_reason"], "")

    def test_passes_when_trend_gaining_and_recommendation_watch(self):
        corrected = pd.DataFrame([_corrected_row("feat_a", "KEEP")])
        stability = pd.DataFrame([_stability_row("feat_a", trend="GAINING", live_recommendation="WATCH")])

        gated = _apply_stability_gate(corrected, stability)

        self.assertEqual(gated.iloc[0]["recommendation"], "KEEP")
        self.assertTrue(gated.iloc[0]["stability_gate_passed"])

    def test_never_upgrades_an_already_failing_feature(self):
        # The gate only ever downgrades — a DELETE/REVIEW recommendation
        # from significance/economic testing is untouched, even with
        # perfect stability evidence.
        corrected = pd.DataFrame([_corrected_row("feat_a", "DELETE", confidence_level=85.0)])
        stability = pd.DataFrame([_stability_row("feat_a", trend="STABLE", live_recommendation="TRADE NOW")])

        gated = _apply_stability_gate(corrected, stability)

        row = gated.iloc[0]
        self.assertEqual(row["recommendation"], "DELETE")
        self.assertEqual(row["confidence_level"], 85.0)
        self.assertFalse(row["stability_gate_passed"])
        self.assertEqual(row["stability_gate_reason"], "")

    def test_multiple_features_gated_independently(self):
        corrected = pd.DataFrame([
            _corrected_row("feat_good", "STRONG KEEP"),
            _corrected_row("feat_decaying", "KEEP"),
            _corrected_row("feat_delete", "DELETE"),
        ])
        stability = pd.DataFrame([
            _stability_row("feat_good", trend="STABLE", live_recommendation="TRADE NOW"),
            _stability_row("feat_decaying", trend="DECAYING", live_recommendation="WATCH"),
            # feat_delete has no stability row at all — irrelevant, it's already DELETE
        ])

        gated = _apply_stability_gate(corrected, stability)
        by_feature = gated.set_index("feature")

        self.assertEqual(by_feature.loc["feat_good", "recommendation"], "STRONG KEEP")
        self.assertEqual(by_feature.loc["feat_decaying", "recommendation"], "REVIEW")
        self.assertEqual(by_feature.loc["feat_delete", "recommendation"], "DELETE")
