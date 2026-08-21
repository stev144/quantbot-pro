# claude code changed: new — dedicated coverage for the two-part
# multiple-testing-correction bug found while running contagion_engine's
# divergence features through the institutional validator, plus the
# separate feature/label leakage bug found alongside it. No mocking, per
# this project's convention: real project CSVs (research_data/*_contagion.csv,
# produced by contagion_engine.run_contagion_research() against real
# data/*.csv) drive the end-to-end tests; the correction-wiring tests build
# real InstitutionalValidationResult instances with deliberately chosen
# p-values rather than mocking any exchange/network call — there is none
# in this module.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.feature_validator import (
    ECONOMIC_SIGNIFICANCE_THRESHOLD,
    IC_ACCEPTABLE,
    FeatureValidator,
    InstitutionalValidationResult,
    apply_family_wide_correction,
)


def _make_result(feature_name, p_value, quartile_spread, ic_overall, stability_std=0.05):
    """Builds a real InstitutionalValidationResult with only the fields the
    correction/recompute pipeline actually reads varying — the rest are
    realistic placeholders."""
    return InstitutionalValidationResult(
        feature_name=feature_name,
        feature_version="testver",
        calculation_timestamp="2026-01-01T00:00:00",
        is_valid=p_value < 0.05,
        p_value=p_value,
        p_value_corrected_bonferroni=p_value,
        p_value_corrected_fdr=p_value,
        t_statistic=0.0,
        mann_whitney_pvalue=p_value,
        block_permutation_pvalue=p_value,   # claude code changed: new required field (Phase B, P1-3) — reuses p_value as a realistic placeholder, same convention as the other fields above
        quartile_spread=quartile_spread,
        is_economically_significant=quartile_spread > ECONOMIC_SIGNIFICANCE_THRESHOLD,
        quartile_returns={1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0},
        win_rate_spread=0.0,
        quartile_win_rates={1: 0.5, 2: 0.5, 3: 0.5, 4: 0.5},
        ic_overall=ic_overall,
        ic_rank="acceptable",
        stability_scores={"2024": ic_overall},
        stability_consistency=stability_std,
        regime_validation={},
        regime_strengths={},
        sharpe_contribution=0.0,
        sharpe_rank="weak",
        return_direction="positive" if ic_overall >= 0 else "negative",
        min_observations_per_quartile=1000,
        null_count=0,
        passed_statistical_significance=p_value < 0.05,
        passed_economic_significance=quartile_spread > ECONOMIC_SIGNIFICANCE_THRESHOLD,
        passed_multiple_testing=p_value < 0.05,
        passed_stability=stability_std < 0.20,
        recommendation="PLACEHOLDER",
        confidence_level=0.0,
    )


class ApplyMultipleTestingCorrectionTest(SimpleTestCase):
    """Regression test for Bug #1: multipletests() only returns real,
    per-feature corrected p-values in ONE call's second slot — the
    original code called it once and mislabeled a scalar
    (alphacBonf) as a second method's p-value array, which crashed
    computing n_fdr and silently forced passes_fdr/passes_bonferroni to
    False for every feature via the except-block fallback."""

    def _build_results_df(self):
        v = FeatureValidator(min_observations=30, alpha=0.05)
        # A deliberately mixed bag: some features overwhelmingly
        # significant, some borderline, one clearly not — enough spread
        # that Bonferroni (divides alpha by N) and FDR (Benjamini-Hochberg)
        # produce genuinely different corrected p-values for the same
        # raw p-value, which is exactly what the old scalar-mislabeling
        # bug could never produce (bonferroni_pvals was never real).
        v.results = [
            _make_result("feat_a", p_value=1e-30, quartile_spread=0.02, ic_overall=0.20),
            _make_result("feat_b", p_value=0.001, quartile_spread=0.01, ic_overall=0.08),
            _make_result("feat_c", p_value=0.03, quartile_spread=0.006, ic_overall=0.03),
            _make_result("feat_d", p_value=0.04, quartile_spread=0.006, ic_overall=0.03),
            _make_result("feat_e", p_value=0.5, quartile_spread=0.0001, ic_overall=0.004),
        ]
        return v, v._results_to_dataframe()

    def test_does_not_raise_and_returns_real_arrays(self):
        v, results_df = self._build_results_df()
        corrected = v._apply_multiple_testing_correction(results_df)

        self.assertIn("p_bonferroni", corrected.columns)
        self.assertIn("p_fdr", corrected.columns)
        # claude code changed: this is the direct regression check for the
        # scalar-mislabeling bug — p_bonferroni must vary per-row (a real
        # per-feature array), not be a single broadcast scalar.
        self.assertGreater(corrected["p_bonferroni"].nunique(), 1)
        self.assertGreater(corrected["p_fdr"].nunique(), 1)

    def test_bonferroni_is_at_least_as_conservative_as_fdr(self):
        v, results_df = self._build_results_df()
        corrected = v._apply_multiple_testing_correction(results_df)

        # Bonferroni is a strictly more conservative correction than
        # Benjamini-Hochberg FDR for the same p-value set — its corrected
        # p-values must never be smaller.
        self.assertTrue((corrected["p_bonferroni"] >= corrected["p_fdr"] - 1e-12).all())

    def test_passes_fdr_matches_threshold_comparison(self):
        v, results_df = self._build_results_df()
        corrected = v._apply_multiple_testing_correction(results_df)

        expected = corrected["p_fdr"] < v.alpha
        self.assertTrue((corrected["passes_fdr"] == expected).all())

    def test_not_every_feature_is_forced_to_fail_correction(self):
        # claude code changed: the old bug's except-block fallback set
        # passes_fdr=False for every single feature unconditionally. With
        # feat_a's p=1e-30, it must survive real FDR correction.
        v, results_df = self._build_results_df()
        corrected = v._apply_multiple_testing_correction(results_df)

        feat_a_row = corrected[corrected["feature"] == "feat_a"].iloc[0]
        self.assertTrue(bool(feat_a_row["passes_fdr"]))


class DetermineRecommendationTest(SimpleTestCase):
    """Unit coverage for the shared decision-tree logic extracted out of
    _validate_single_feature_institutional() so the provisional
    (pre-correction) and final (post-correction) passes share one
    implementation rather than two copies that could drift apart."""

    def test_all_criteria_pass_with_strong_ic_is_strong_keep(self):
        rec, conf = FeatureValidator._determine_recommendation(
            passed_significance=True,
            passed_economic=True,
            passed_stability=True,
            quartile_spread=0.01,
            stability_consistency=0.05,
            ic_overall=IC_ACCEPTABLE + 0.05,
        )
        self.assertEqual(rec, "STRONG KEEP")
        self.assertEqual(conf, 95.0)

    def test_all_criteria_pass_with_moderate_ic_is_keep(self):
        rec, conf = FeatureValidator._determine_recommendation(
            passed_significance=True,
            passed_economic=True,
            passed_stability=True,
            quartile_spread=0.01,
            stability_consistency=0.05,
            ic_overall=0.03,
        )
        self.assertEqual(rec, "KEEP")
        self.assertEqual(conf, 75.0)

    def test_failing_economic_significance_alone_is_review(self):
        rec, conf = FeatureValidator._determine_recommendation(
            passed_significance=True,
            passed_economic=False,
            passed_stability=True,
            quartile_spread=0.0005,
            stability_consistency=0.05,
            ic_overall=0.015,
        )
        self.assertEqual(rec, "REVIEW")
        self.assertEqual(conf, 50.0)

    def test_failing_significance_and_economic_is_delete(self):
        rec, conf = FeatureValidator._determine_recommendation(
            passed_significance=False,
            passed_economic=False,
            passed_stability=True,
            quartile_spread=0.0001,
            stability_consistency=0.05,
            ic_overall=0.004,
        )
        self.assertEqual(rec, "DELETE")
        self.assertEqual(conf, 85.0)


class RecomputeRecommendationsAfterCorrectionTest(SimpleTestCase):
    """Regression test for Bug #2: recommendation/confidence_level were
    finalized in _validate_single_feature_institutional() using ONLY the
    raw, uncorrected p-value, and nothing downstream ever recomputed them
    after real FDR correction became available — so passes_fdr never
    actually influenced the final verdict despite a comment claiming
    otherwise."""

    def test_recommendation_flips_when_fdr_correction_fails_a_borderline_feature(self):
        v = FeatureValidator(min_observations=30, alpha=0.05)
        # feat_borderline's raw p=0.045 clears alpha=0.05 on its own — a
        # provisional-only pipeline would call it significant. Packed
        # alongside 3 features with far smaller p-values and 6 clearly
        # non-significant ones (m=10 total), Benjamini-Hochberg FDR's
        # step-up procedure evaluates feat_borderline at rank 4:
        # threshold = (4/10)*0.05 = 0.02, and 0.045 > 0.02, so it fails
        # correction (confirmed directly against statsmodels: corrected
        # p_fdr=0.1125). If recommendation were still being decided from
        # the raw p-value alone (the bug), this feature would incorrectly
        # keep a "passed significance" verdict.
        v.results = [
            _make_result("feat_tiny_1", p_value=1e-30, quartile_spread=0.02, ic_overall=0.20),
            _make_result("feat_tiny_2", p_value=1e-25, quartile_spread=0.02, ic_overall=0.18),
            _make_result("feat_tiny_3", p_value=1e-20, quartile_spread=0.02, ic_overall=0.17),
            _make_result("feat_borderline", p_value=0.045, quartile_spread=0.01, ic_overall=0.06),
            _make_result("feat_noise_1", p_value=0.3, quartile_spread=0.0001, ic_overall=0.005),
            _make_result("feat_noise_2", p_value=0.4, quartile_spread=0.0001, ic_overall=0.005),
            _make_result("feat_noise_3", p_value=0.5, quartile_spread=0.0001, ic_overall=0.005),
            _make_result("feat_noise_4", p_value=0.6, quartile_spread=0.0001, ic_overall=0.005),
            _make_result("feat_noise_5", p_value=0.7, quartile_spread=0.0001, ic_overall=0.005),
            _make_result("feat_noise_6", p_value=0.8, quartile_spread=0.0001, ic_overall=0.005),
        ]
        results_df = v._results_to_dataframe()
        corrected = v._apply_multiple_testing_correction(results_df)
        final = v._recompute_recommendations_after_correction(corrected)

        borderline = final[final["feature"] == "feat_borderline"].iloc[0]
        self.assertLess(0.045, v.alpha)  # sanity: raw p passes on its own
        self.assertGreater(borderline["p_fdr"], v.alpha)  # but corrected p fails
        self.assertFalse(bool(borderline["passed_multiple_testing"]))
        self.assertNotIn(borderline["recommendation"], ("STRONG KEEP", "KEEP"))

    def test_passed_multiple_testing_column_equals_passes_fdr(self):
        v = FeatureValidator(min_observations=30, alpha=0.05)
        v.results = [
            _make_result("feat_a", p_value=1e-30, quartile_spread=0.02, ic_overall=0.20),
            _make_result("feat_b", p_value=0.9, quartile_spread=0.0001, ic_overall=0.001),
        ]
        results_df = v._results_to_dataframe()
        corrected = v._apply_multiple_testing_correction(results_df)
        final = v._recompute_recommendations_after_correction(corrected)

        self.assertTrue((final["passed_multiple_testing"] == final["passes_fdr"]).all())

    def test_no_op_when_correction_columns_absent(self):
        # Defensive: if passes_fdr is missing (e.g. correction failed
        # upstream in some future refactor), this must pass the frame
        # through unchanged rather than raising.
        v = FeatureValidator(min_observations=30, alpha=0.05)
        df = pd.DataFrame({"feature": ["x"], "recommendation": ["KEEP"], "confidence_level": [75.0]})
        result = v._recompute_recommendations_after_correction(df)
        pd.testing.assert_frame_equal(result, df)


class ContagionFeatureLabelLeakageTest(SimpleTestCase):
    """Regression test for the feature/label leakage bug found alongside
    the correction bug: the old exclude_cols was an enumerated set
    (forward_return_1h/4h/24h, win_1h/4h/24h) that didn't cover every
    horizon the research pipeline actually produces — contagion_engine.py
    emits forward_return_2h/12h too, which slipped through as
    "features" and got validated against themselves as the label
    (confirmed empirically: IC=1.0, "STRONG KEEP" for forward_return_2h
    when forward_return_2h was the label column)."""

    def test_other_forward_return_horizons_excluded_from_feature_columns(self):
        df = pd.read_csv("research_data/DOGE_USDT_contagion.csv")
        v = FeatureValidator(min_observations=30, alpha=0.05)
        results_df = v.validate_all_features_institutional(df, forward_return_col="forward_return_2h")

        validated_features = set(results_df["feature"])
        leaking_labels = {"forward_return_1h", "forward_return_2h", "forward_return_4h", "forward_return_12h"}
        self.assertEqual(validated_features & leaking_labels, set())

    def test_no_feature_has_perfect_ic_with_itself(self):
        # A feature validated against itself as the label produces IC=1.0
        # — a value that should never occur for a genuine, independent
        # feature. This is the direct symptom the leakage bug produced.
        df = pd.read_csv("research_data/DOGE_USDT_contagion.csv")
        v = FeatureValidator(min_observations=30, alpha=0.05)
        results_df = v.validate_all_features_institutional(df, forward_return_col="forward_return_2h")

        self.assertTrue((results_df["ic_overall"].abs() < 0.999).all())

    def test_generic_prefix_exclusion_covers_horizons_beyond_the_old_enumerated_list(self):
        # claude code changed: proves the fix is a real prefix match, not
        # a slightly-longer enumerated list that would still miss a future
        # horizon. Uses a synthetic df with a horizon no engine in this
        # project currently emits (7h) to isolate the exclusion logic
        # itself from any one engine's real output.
        import numpy as np

        n = 500
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "close": 100 + rng.normal(size=n).cumsum(),
            "real_feature": rng.normal(size=n),
            "forward_return_7h": rng.normal(size=n) * 0.01,
            "win_7h": rng.integers(0, 2, size=n).astype(float),
        })

        v = FeatureValidator(min_observations=10, alpha=0.05)
        results_df = v.validate_all_features_institutional(df, forward_return_col="forward_return_7h")

        validated_features = set(results_df["feature"])
        self.assertNotIn("forward_return_7h", validated_features)
        self.assertNotIn("win_7h", validated_features)
        self.assertIn("real_feature", validated_features)


# claude code changed: new — Phase B of the controlled remediation program
# (forensic-audit finding P1-3): Mann-Whitney/t-test assume i.i.d. samples,
# which overlapping forward_return_4h-style labels violate. These tests
# cover the block-permutation replacement.
class BlockPermutationSignificanceTest(SimpleTestCase):

    def _make_df(self, n, seed, relationship="none"):
        rng = np.random.default_rng(seed)
        returns = rng.normal(0, 0.01, n)
        if relationship == "strong":
            # Feature is (almost) the return itself — real, strong signal.
            feature = returns * 100 + rng.normal(0, 0.0001, n)
        else:
            # Feature is pure independent noise — no real relationship.
            feature = rng.normal(0, 1, n)
        return pd.DataFrame({
            "real_feature": feature,
            "forward_return_4h": returns,
        })

    def test_parse_horizon_candles_reads_the_label_suffix(self):
        self.assertEqual(FeatureValidator._parse_horizon_candles("forward_return_4h"), 4)
        self.assertEqual(FeatureValidator._parse_horizon_candles("forward_return_24h"), 24)
        self.assertEqual(FeatureValidator._parse_horizon_candles("forward_return_1h"), 1)

    def test_parse_horizon_candles_falls_back_on_unparseable_name(self):
        from bot.research.feature_validator import DEFAULT_HORIZON_CANDLES_FALLBACK
        self.assertEqual(
            FeatureValidator._parse_horizon_candles("some_custom_label"),
            DEFAULT_HORIZON_CANDLES_FALLBACK,
        )

    def test_null_feature_gets_a_non_trivial_block_permutation_pvalue(self):
        # A feature with genuinely no relationship to returns should NOT
        # get an artificially tiny p-value — the whole point of the fix.
        df = self._make_df(n=2000, seed=11, relationship="none")
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=42)
        results_df = v.validate_all_features_raw(df, forward_return_col="forward_return_4h")

        row = results_df[results_df["feature"] == "real_feature"].iloc[0]
        self.assertGreater(row["p_value_block_permutation"], 0.05)

    def test_strong_feature_gets_a_small_block_permutation_pvalue(self):
        df = self._make_df(n=2000, seed=12, relationship="strong")
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=42)
        results_df = v.validate_all_features_raw(df, forward_return_col="forward_return_4h")

        row = results_df[results_df["feature"] == "real_feature"].iloc[0]
        self.assertLess(row["p_value_block_permutation"], 0.05)

    def test_block_permutation_pvalue_never_zero(self):
        # Empirical p-value convention: (extreme_count + 1) / (n + 1) —
        # matches PermutationTestEngine._compare(), never exactly zero
        # since the real result is itself one possible draw.
        df = self._make_df(n=2000, seed=13, relationship="strong")
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=7)
        results_df = v.validate_all_features_raw(df, forward_return_col="forward_return_4h")

        row = results_df[results_df["feature"] == "real_feature"].iloc[0]
        self.assertGreater(row["p_value_block_permutation"], 0.0)

    def test_block_shuffle_preserves_multiset_of_values(self):
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=1)
        arr = np.arange(97, dtype=float)   # deliberately not a multiple of block_size
        shuffled = v._block_shuffle_1d(arr, block_size=5)
        self.assertEqual(len(shuffled), len(arr))
        np.testing.assert_array_equal(np.sort(shuffled), np.sort(arr))


# claude code changed: new — Phase B (P1-2). Proves the family-wide
# correction scope fix: a borderline feature that would pass FDR
# correction when scoped to just its own small symbol-only family can
# correctly fail once pooled with the full research run's other symbols —
# this is the exact bug being fixed, demonstrated end-to-end.
class FamilyWideCorrectionTest(SimpleTestCase):

    def test_pooling_makes_correction_stricter_for_a_borderline_feature(self):
        v = FeatureValidator(alpha=0.05)

        # symbol_A alone: m=2. BH critical value for the rank-2 p-value
        # (0.045) is (2/2)*0.05 = 0.05 -> 0.045 < 0.05 -> PASSES alone.
        v.results = [
            _make_result("f_tiny", p_value=1e-30, quartile_spread=0.02, ic_overall=0.20),
            _make_result("f_borderline", p_value=0.045, quartile_spread=0.01, ic_overall=0.06),
        ]
        symbol_a_raw = v._results_to_dataframe()

        # Per-symbol-only correction (the OLD, buggy scope) — passes.
        symbol_a_only_corrected = v._apply_multiple_testing_correction(symbol_a_raw)
        old_scope_passes = bool(
            symbol_a_only_corrected.loc[
                symbol_a_only_corrected["feature"] == "f_borderline", "passes_fdr"
            ].iloc[0]
        )
        self.assertTrue(old_scope_passes, "sanity check: must pass within its own tiny family")

        # Three more "symbols" of pure noise features, pooled alongside A —
        # the real hypothesis family run_research_all.py actually tests.
        raw_by_symbol = {"symbol_A": symbol_a_raw}
        for sym in ("symbol_B", "symbol_C", "symbol_D"):
            v2 = FeatureValidator(alpha=0.05)
            v2.results = [
                _make_result(f"{sym}_noise_{i}", p_value=0.3 + i * 0.1,
                             quartile_spread=0.0001, ic_overall=0.005)
                for i in range(6)
            ]
            raw_by_symbol[sym] = v2._results_to_dataframe()

        per_symbol_corrected, pooled = apply_family_wide_correction(raw_by_symbol, alpha=0.05)

        self.assertEqual(len(pooled), 20)   # 2 + 6 + 6 + 6

        new_scope_passes = bool(
            per_symbol_corrected["symbol_A"].loc[
                per_symbol_corrected["symbol_A"]["feature"] == "f_borderline", "passes_fdr"
            ].iloc[0]
        )
        self.assertFalse(
            new_scope_passes,
            "the same p-value must fail once corrected against the true, wider hypothesis family",
        )

    def test_per_symbol_split_preserves_row_count_and_drops_tag_column(self):
        raw_by_symbol = {}
        for sym in ("symbol_X", "symbol_Y"):
            v = FeatureValidator(alpha=0.05)
            v.results = [
                _make_result(f"{sym}_f1", p_value=0.01, quartile_spread=0.01, ic_overall=0.1),
                _make_result(f"{sym}_f2", p_value=0.5, quartile_spread=0.0001, ic_overall=0.005),
            ]
            raw_by_symbol[sym] = v._results_to_dataframe()

        per_symbol_corrected, pooled = apply_family_wide_correction(raw_by_symbol, alpha=0.05)

        self.assertEqual(set(per_symbol_corrected.keys()), {"symbol_X", "symbol_Y"})
        for sym, df in per_symbol_corrected.items():
            self.assertEqual(len(df), 2)
            self.assertNotIn("symbol", df.columns)
        self.assertIn("symbol", pooled.columns)
        self.assertEqual(len(pooled), 4)


class ValidateAllFeaturesRawSplitTest(SimpleTestCase):
    """Phase B (P1-2): validate_all_features_raw() must produce
    provisional, uncorrected results; validate_all_features_institutional()
    must still work end-to-end as a single-call convenience wrapper
    (e.g. for an already-pooled multi-symbol observations.csv)."""

    def _make_df(self, n=500, seed=21):
        rng = np.random.default_rng(seed)
        returns = rng.normal(0, 0.01, n)
        return pd.DataFrame({
            "feat_a": returns * 50 + rng.normal(0, 0.001, n),
            "feat_b": rng.normal(0, 1, n),
            "forward_return_4h": returns,
        })

    def test_raw_has_no_correction_columns(self):
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=1)
        raw = v.validate_all_features_raw(self._make_df(), forward_return_col="forward_return_4h")

        self.assertNotIn("passes_fdr", raw.columns)
        self.assertNotIn("p_fdr", raw.columns)
        self.assertIn("p_value_block_permutation", raw.columns)

    def test_institutional_wrapper_still_applies_correction_end_to_end(self):
        v = FeatureValidator(min_observations=30, alpha=0.05, random_seed=1)
        final = v.validate_all_features_institutional(self._make_df(), forward_return_col="forward_return_4h")

        self.assertIn("passes_fdr", final.columns)
        self.assertIn("recommendation", final.columns)
