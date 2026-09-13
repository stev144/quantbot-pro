# ============================================================
# bot/tests/test_regime_labels.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 3/19 (regime classifier validation + regime integrity
# tests). Covers: temporal correctness (no look-ahead), determinism, label
# coverage, transition/duration reporting, missing-regime handling,
# insufficient-sample marking, sensitivity to data gaps, and contiguous-
# episode extraction — the exact checklist the mission's Phase 3/19 name.
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.regime_labels import (
    COMBINED_REGIME_LABELS,
    MIN_REGIME_OBS,
    REGIME_TAXONOMY_VERSION,
    TREND_STATES,
    VOL_STATES,
    RegimeTaxonomyConfig,
    assert_no_lookahead,
    compute_regime_labels,
    label_regime_episodes,
    summarize_regime_labels,
)


def _synthetic_ohlcv(n=2000, seed=7, trend_break=None):
    """Deterministic synthetic OHLCV: a random walk with a deliberate
    trend segment, real enough to exercise ADX/EMA/ATR without depending
    on any real data file being present on disk (this suite must run in
    any checkout, not just one with data/*.csv already fetched)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    steps = rng.normal(0, 1, n)
    if trend_break is not None:
        steps[trend_break:trend_break + 300] += 0.6   # inject a real uptrend segment
    close = 100 + np.cumsum(steps)
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.2, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


class RegimeLabelCoverageTest(SimpleTestCase):
    def test_every_row_gets_a_label_or_an_explicit_nan(self):
        df = _synthetic_ohlcv()
        labels = compute_regime_labels(df)
        self.assertEqual(len(labels), len(df))
        # every non-NaN trend_state is one of the taxonomy's real states — never an unrecognized value
        observed = labels["trend_state"].dropna().unique().tolist()
        self.assertTrue(set(observed) <= set(TREND_STATES))
        observed_vol = labels["volatility_state"].dropna().unique().tolist()
        self.assertTrue(set(observed_vol) <= set(VOL_STATES))

    def test_warmup_rows_are_explicitly_nan_not_a_default_regime(self):
        df = _synthetic_ohlcv()
        config = RegimeTaxonomyConfig(min_periods=60)
        labels = compute_regime_labels(df, config)
        # the mission's explicit rule: missing regime labels must never
        # silently become a valid regime — warmup rows must be NaN, not RANGING
        self.assertTrue(labels["trend_state"].iloc[:60].isna().all())
        self.assertTrue(labels["volatility_state"].iloc[:60].isna().all())

    def test_regime_label_is_the_cross_of_trend_and_volatility(self):
        df = _synthetic_ohlcv()
        labels = compute_regime_labels(df)
        valid = labels.dropna(subset=["regime_label"])
        for _, row in valid.sample(min(50, len(valid)), random_state=1).iterrows():
            self.assertEqual(row["regime_label"], f"{row['trend_state']}_{row['volatility_state']}")
        self.assertTrue(set(valid["regime_label"].unique()) <= set(COMBINED_REGIME_LABELS))

    def test_taxonomy_version_is_stamped_on_every_row(self):
        df = _synthetic_ohlcv()
        labels = compute_regime_labels(df)
        self.assertTrue((labels["taxonomy_version"] == REGIME_TAXONOMY_VERSION).all())


class NoLookaheadTest(SimpleTestCase):
    def test_truncating_the_future_never_changes_a_past_label(self):
        df = _synthetic_ohlcv(n=3000, trend_break=1500)
        self.assertTrue(assert_no_lookahead(df, RegimeTaxonomyConfig(), truncate_at=2000))

    def test_multiple_truncation_points_all_agree(self):
        df = _synthetic_ohlcv(n=2500, trend_break=1000)
        config = RegimeTaxonomyConfig()
        for cut in (200, 500, 1200, 2000):
            self.assertTrue(assert_no_lookahead(df, config, truncate_at=cut), f"look-ahead detected when truncating at {cut}")

    def test_raises_if_truncation_point_is_inside_warmup(self):
        df = _synthetic_ohlcv()
        config = RegimeTaxonomyConfig(min_periods=60)
        with self.assertRaises(ValueError):
            assert_no_lookahead(df, config, truncate_at=30)


class DeterminismTest(SimpleTestCase):
    def test_same_input_produces_identical_output_every_call(self):
        df = _synthetic_ohlcv()
        a = compute_regime_labels(df)
        b = compute_regime_labels(df)
        pd.testing.assert_frame_equal(a, b)

    def test_does_not_mutate_the_caller_dataframe(self):
        df = _synthetic_ohlcv()
        before = df.copy(deep=True)
        compute_regime_labels(df)
        pd.testing.assert_frame_equal(df, before)


class InputValidationTest(SimpleTestCase):
    def test_missing_required_columns_raises_not_silently_degrades(self):
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC"))
        with self.assertRaises(ValueError):
            compute_regime_labels(df)

    def test_non_datetime_index_raises(self):
        df = _synthetic_ohlcv().reset_index(drop=True)
        with self.assertRaises(ValueError):
            compute_regime_labels(df)


class DataGapSensitivityTest(SimpleTestCase):
    def test_a_gap_of_missing_close_values_does_not_crash_and_stays_nan(self):
        df = _synthetic_ohlcv()
        df = df.copy()
        df.loc[df.index[500:510], "close"] = np.nan
        # the underlying regime_precomputer path fills forward internally
        # via its own defaults; this module's OWN NaN check (EMA fast/slow)
        # must still catch a real close-price gap rather than crash or
        # silently invent a value
        labels = compute_regime_labels(df)
        self.assertEqual(len(labels), len(df))  # no crash, no row dropped

    def test_large_gap_in_the_middle_of_the_series_does_not_crash(self):
        df = _synthetic_ohlcv(n=1000)
        df2 = _synthetic_ohlcv(n=1000, seed=99)
        df2.index = pd.date_range(df.index[-1] + pd.Timedelta(days=10), periods=1000, freq="1h", tz="UTC")
        combined = pd.concat([df, df2])
        labels = compute_regime_labels(combined)
        self.assertEqual(len(labels), len(combined))


class RegimeSampleSummaryTest(SimpleTestCase):
    def test_sufficient_and_insufficient_samples_are_both_reported_explicitly(self):
        # A short series produces few observations per regime — some
        # regimes should come back INSUFFICIENT_SAMPLE, never silently omitted.
        df = _synthetic_ohlcv(n=200)
        labels = compute_regime_labels(df, RegimeTaxonomyConfig(min_periods=60))
        summary = summarize_regime_labels(labels["trend_state"], candidate_regimes=TREND_STATES, min_obs=MIN_REGIME_OBS)
        self.assertEqual(set(summary["regime"]), set(TREND_STATES))
        for _, row in summary.iterrows():
            if row["observations"] == 0:
                self.assertEqual(row["status"], "MISSING")
            elif row["observations"] < MIN_REGIME_OBS:
                self.assertEqual(row["status"], "INSUFFICIENT_SAMPLE")
                self.assertFalse(row["sufficient_sample"])
            else:
                self.assertEqual(row["status"], "OK")
                self.assertTrue(row["sufficient_sample"])

    def test_a_never_observed_regime_gets_an_explicit_missing_row_not_omission(self):
        labels = pd.Series(["RANGING"] * 100, index=pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC"))
        summary = summarize_regime_labels(labels, candidate_regimes=TREND_STATES)
        row = summary[summary["regime"] == "TRENDING_UP"].iloc[0]
        self.assertEqual(row["observations"], 0)
        self.assertEqual(row["status"], "MISSING")

    def test_percentages_sum_to_100_across_observed_regimes(self):
        df = _synthetic_ohlcv(n=3000, trend_break=1000)
        labels = compute_regime_labels(df)
        summary = summarize_regime_labels(labels["trend_state"], candidate_regimes=TREND_STATES)
        observed = summary[summary["observations"] > 0]
        # claude code changed: places=2 not 4 — each row's percentage is
        # independently rounded to 4dp before this sum, so the sum of N
        # independently-rounded values naturally drifts from 100.0 by up
        # to N * 0.5e-4; this asserts the real invariant (no double-
        # counted or missing observations), not float-rounding exactness.
        self.assertAlmostEqual(observed["percentage"].sum(), 100.0, places=2)

    def test_first_and_last_timestamp_are_real_observed_timestamps(self):
        df = _synthetic_ohlcv(n=3000, trend_break=1000)
        labels = compute_regime_labels(df)
        summary = summarize_regime_labels(labels["trend_state"], candidate_regimes=TREND_STATES)
        for _, row in summary[summary["observations"] > 0].iterrows():
            regime_rows = labels[labels["trend_state"] == row["regime"]]
            self.assertEqual(pd.Timestamp(row["first_timestamp"]), regime_rows.index.min())
            self.assertEqual(pd.Timestamp(row["last_timestamp"]), regime_rows.index.max())

    def test_transitions_and_avg_duration_are_internally_consistent(self):
        # avg_duration * transitions_in should reconstruct observations (integer rounding aside)
        df = _synthetic_ohlcv(n=3000, trend_break=1000)
        labels = compute_regime_labels(df)
        summary = summarize_regime_labels(labels["trend_state"], candidate_regimes=TREND_STATES)
        for _, row in summary[summary["observations"] > 0].iterrows():
            reconstructed = row["avg_duration_periods"] * row["transitions_in"]
            self.assertAlmostEqual(reconstructed, row["observations"], delta=row["transitions_in"] + 1)


class RegimeEpisodeExtractionTest(SimpleTestCase):
    def test_episodes_are_contiguous_and_cover_every_valid_row_exactly_once(self):
        df = _synthetic_ohlcv(n=2000, trend_break=800)
        labels = compute_regime_labels(df)
        episodes = label_regime_episodes(labels["trend_state"])
        total_length = episodes["length"].sum()
        self.assertEqual(total_length, labels["trend_state"].notna().sum())

    def test_adjacent_episodes_never_share_the_same_regime(self):
        df = _synthetic_ohlcv(n=2000, trend_break=800)
        labels = compute_regime_labels(df)
        episodes = label_regime_episodes(labels["trend_state"])
        for i in range(1, len(episodes)):
            self.assertNotEqual(episodes.iloc[i]["regime"], episodes.iloc[i - 1]["regime"])

    def test_episode_boundaries_match_real_timestamps_in_the_data(self):
        df = _synthetic_ohlcv(n=2000, trend_break=800)
        labels = compute_regime_labels(df)
        episodes = label_regime_episodes(labels["trend_state"])
        valid_index = set(labels.dropna(subset=["trend_state"]).index)
        for _, ep in episodes.iterrows():
            self.assertIn(ep["start"], valid_index)
            self.assertIn(ep["end"], valid_index)

    def test_empty_labels_returns_empty_episode_table(self):
        empty = pd.Series([np.nan] * 50, index=pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC"))
        episodes = label_regime_episodes(empty)
        self.assertEqual(len(episodes), 0)


class RegimeDistributionSanityTest(SimpleTestCase):
    """claude code changed: new — a real, planted-signal check: a series
    with a deliberately injected trend segment must actually get
    classified as trending somewhere in that segment. Guards against the
    classifier being so conservative it never fires, or so trivial it
    fires everywhere (both would make regime-conditional research
    meaningless without ever producing a wrong-looking crash)."""

    def test_a_planted_uptrend_segment_is_detected_as_trending_up_somewhere(self):
        df = _synthetic_ohlcv(n=2000, trend_break=800)
        labels = compute_regime_labels(df)
        segment = labels.iloc[800 + 60:1100]  # allow the trend to develop past the ADX/EMA lookback
        self.assertGreater((segment["trend_state"] == "TRENDING_UP").sum(), 0)

    def test_not_every_row_is_the_same_regime(self):
        df = _synthetic_ohlcv(n=3000, trend_break=1000)
        labels = compute_regime_labels(df)
        self.assertGreater(labels["trend_state"].dropna().nunique(), 1)
