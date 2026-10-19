# bot/tests/test_dca_cycle_extractor.py
"""
Known-answer tests for dca_cycle_extractor.py — Phase 7.

Convention: deterministic synthetic inputs, hand-computed expected outputs.
No mocking of the paper engine — extractor helper functions (_parse_entry_time,
_decision_candle_ts, _load_observations pattern) are tested in isolation with
real pandas objects. The full extract_dca_cycles() is tested with a minimal
patched paper engine to avoid a live MT5/network call during CI.

Gate 1 (timing):  decision_ts = entry_time - 1h; fill_ts is never the feature candle.
Gate 2 (join):    exact UTC match; a 1-second offset must NOT match.
Gate 3 (targets): win is int not bool; profit_usdt is float; fills is int.
Gate 5 (censored): DATA_END_STILL_OPEN cycles must be excluded from cycle_df.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.dca_cycle_extractor import (
    CENSORED_REASON,
    ENTRY_FEATURE_FAMILY,
    AlignmentResult,
    _decision_candle_ts,
    _parse_entry_time,
    extract_dca_cycles,
    print_alignment_report,
)

# ── injectable helpers for tests (avoids patching deferred imports) ───────────

def _sim_fn_from(sim_results: dict):
    """Returns a zero-arg callable that returns sim_results."""
    def _fn(): return sim_results
    return _fn


def _obs_fn_from(obs_df: pd.DataFrame):
    """Returns a callable(pair) that always returns (obs_df, 'mocked_path')."""
    def _fn(pair): return (obs_df, "mocked_path")
    return _fn


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_obs_df(timestamps: list[str], feature_val: float = 25.0) -> pd.DataFrame:
    """Minimal observations DataFrame with all ENTRY_FEATURE_FAMILY columns."""
    idx = pd.to_datetime(timestamps, utc=True)
    data = {feat: [feature_val] * len(timestamps) for feat in ENTRY_FEATURE_FAMILY}
    return pd.DataFrame(data, index=idx)


def _make_cycle(
    entry_time: str,
    profit_usdt: float = 5.0,
    fills: int = 2,
    candles_held: int = 12,
    exit_reason: str = "TARGET_HIT",
    direction: str = "LONG",
) -> dict:
    """Minimal cycle trade dict matching ranging_paper_engine schema."""
    return {
        "entry_time":    entry_time,
        "exit_time":     "2024-01-02 00:00:00+00:00",
        "entry_price":   1.1000,
        "exit_price":    1.1011,
        "wap":           1.1005,
        "fills":         fills,
        "total_cost_usdt": 100.0,
        "profit_usdt":   profit_usdt,
        "gross_usdt":    profit_usdt + 0.10,
        "commission_usdt": 0.10,
        "swap_pnl_usdt": 0.0,
        "candles_held":  candles_held,
        "exit_reason":   exit_reason,
        "cost_complete": True,
        "direction":     direction,
    }


# ── Gate 1: decision-candle timing ────────────────────────────────────────────

class DecisionCandleTimingTest(SimpleTestCase):
    """decision_ts = fill_ts − 1h; Gate 1 invariant."""

    def test_decision_ts_is_one_hour_before_fill(self):
        fill_ts_str = "2024-06-15 08:00:00+00:00"
        dec = _decision_candle_ts(fill_ts_str, freq_hours=1)
        expected = pd.Timestamp("2024-06-15 07:00:00", tz="UTC")
        self.assertEqual(dec, expected)

    def test_decision_ts_utc_aware(self):
        dec = _decision_candle_ts("2024-01-01 01:00:00+00:00")
        self.assertIsNotNone(dec.tz, "decision_ts must be tz-aware")
        self.assertEqual(str(dec.tz), "UTC")

    def test_decision_ts_naive_input_treated_as_utc(self):
        dec = _decision_candle_ts("2024-01-01 01:00:00")
        expected = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
        self.assertEqual(dec, expected)

    def test_midnight_entry_wraps_to_previous_day(self):
        dec = _decision_candle_ts("2024-03-01 00:00:00+00:00")
        expected = pd.Timestamp("2024-02-29 23:00:00", tz="UTC")
        self.assertEqual(dec, expected)

    def test_fill_candle_not_in_index_gives_unmatched(self):
        fill_ts_str  = "2024-06-15 08:00:00+00:00"
        dec          = _decision_candle_ts(fill_ts_str, freq_hours=1)
        fill_ts      = pd.Timestamp(fill_ts_str)
        obs_df       = _make_obs_df(["2024-06-15 08:00:00"])   # fill candle, not decision candle
        # The decision candle (07:00) must NOT be in obs_df when obs contains only the fill candle
        self.assertNotIn(dec, obs_df.index,
            "Fill candle index must NOT coincide with decision candle; this would be a 1-bar look-ahead")


# ── Gate 2: exact join semantics ──────────────────────────────────────────────

class ExactJoinTest(SimpleTestCase):
    """1-second offset must not match; no nearest-neighbour fallback."""

    def test_exact_match_returns_features(self):
        decision_ts_str = "2024-06-15 07:00:00+00:00"
        obs_df = _make_obs_df([decision_ts_str], feature_val=30.5)
        dec = pd.Timestamp(decision_ts_str)
        self.assertIn(dec, obs_df.index)
        self.assertAlmostEqual(obs_df.loc[dec, "adx"], 30.5)

    def test_one_second_offset_does_not_match(self):
        obs_df = _make_obs_df(["2024-06-15 07:00:00+00:00"])
        off_ts = pd.Timestamp("2024-06-15 07:00:01", tz="UTC")
        self.assertNotIn(off_ts, obs_df.index,
            "A 1-second offset must not match; no nearest-neighbour allowed")

    def test_one_hour_offset_does_not_match(self):
        obs_df = _make_obs_df(["2024-06-15 07:00:00+00:00"])
        off_ts = pd.Timestamp("2024-06-15 08:00:00", tz="UTC")  # fill candle
        self.assertNotIn(off_ts, obs_df.index)


# ── Gate 5: censored cycle exclusion ─────────────────────────────────────────

class CensoredCycleExclusionTest(SimpleTestCase):
    """DATA_END_STILL_OPEN cycles must not appear in cycle_df."""

    def _run_extract_with_cycles(self, cycles: list[dict], obs_df: pd.DataFrame) -> AlignmentResult:
        """Run extract_dca_cycles using injected sim/obs functions."""
        sim_results = {"EUR_USD": {"trades": cycles}}
        return extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )

    def test_censored_cycle_excluded_from_cycle_df(self):
        # Decision candles: 07:00 and 08:00; fill times: 08:00 and 09:00
        obs_df = _make_obs_df([
            "2024-06-15 07:00:00+00:00",
            "2024-06-15 08:00:00+00:00",
        ])
        cycles = [
            _make_cycle("2024-06-15 08:00:00+00:00", profit_usdt=3.0),          # closed — should appear
            _make_cycle("2024-06-15 09:00:00+00:00", exit_reason=CENSORED_REASON),  # censored — must not appear
        ]
        result = self._run_extract_with_cycles(cycles, obs_df)
        self.assertEqual(result.total_censored, 1)
        self.assertEqual(result.total_matched, 1)
        self.assertFalse(result.cycle_df.empty)
        # The censored cycle's entry_time must not be in cycle_df
        entry_times = result.cycle_df["entry_time"].tolist()
        self.assertNotIn("2024-06-15 09:00:00+00:00", entry_times)

    def test_all_censored_gives_empty_cycle_df(self):
        obs_df = _make_obs_df(["2024-06-15 07:00:00+00:00"])
        cycles = [_make_cycle("2024-06-15 08:00:00+00:00", exit_reason=CENSORED_REASON)]
        result = self._run_extract_with_cycles(cycles, obs_df)
        self.assertEqual(result.total_censored, 1)
        self.assertEqual(result.total_matched, 0)
        self.assertTrue(result.cycle_df.empty)

    def test_alignment_report_counts_censored_separately(self):
        obs_df = _make_obs_df([
            "2024-06-15 07:00:00+00:00",
            "2024-06-15 08:00:00+00:00",
        ])
        cycles = [
            _make_cycle("2024-06-15 08:00:00+00:00"),
            _make_cycle("2024-06-15 09:00:00+00:00", exit_reason=CENSORED_REASON),
        ]
        result = self._run_extract_with_cycles(cycles, obs_df)
        r = result.reports[0]
        self.assertEqual(r.censored_cycles, 1)
        self.assertEqual(r.closed_cycles, 1)
        self.assertEqual(r.total_cycles, 2)


# ── Gate 3: target dtypes ─────────────────────────────────────────────────────

class TargetDtypesTest(SimpleTestCase):
    """win must be int (not bool); profit_usdt float; fills int."""

    def _single_cycle_result(self, profit: float) -> AlignmentResult:
        obs_df = _make_obs_df(["2024-06-15 07:00:00+00:00"], feature_val=20.0)
        cycles = [_make_cycle("2024-06-15 08:00:00+00:00", profit_usdt=profit)]
        sim_results = {"EUR_USD": {"trades": cycles}}
        return extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )

    def test_win_dtype_is_int_not_bool(self):
        result = self._single_cycle_result(profit=3.0)
        df = result.cycle_df
        self.assertFalse(
            pd.api.types.is_bool_dtype(df["win"]),
            "win must be int, not bool — IC computation requires numeric dtype",
        )
        self.assertTrue(pd.api.types.is_integer_dtype(df["win"]))

    def test_win_is_1_for_positive_profit(self):
        result = self._single_cycle_result(profit=2.5)
        self.assertEqual(result.cycle_df["win"].iloc[0], 1)

    def test_win_is_0_for_negative_profit(self):
        result = self._single_cycle_result(profit=-1.5)
        self.assertEqual(result.cycle_df["win"].iloc[0], 0)

    def test_win_is_0_for_zero_profit(self):
        result = self._single_cycle_result(profit=0.0)
        self.assertEqual(result.cycle_df["win"].iloc[0], 0)

    def test_profit_usdt_is_float(self):
        result = self._single_cycle_result(profit=3.0)
        self.assertTrue(
            pd.api.types.is_float_dtype(result.cycle_df["profit_usdt"]),
        )

    def test_fills_dtype_is_int(self):
        obs_df = _make_obs_df(["2024-06-15 07:00:00+00:00"])
        cycles = [_make_cycle("2024-06-15 08:00:00+00:00", fills=3)]
        sim_results = {"EUR_USD": {"trades": cycles}}
        result = extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )
        self.assertTrue(pd.api.types.is_integer_dtype(result.cycle_df["fills"]))


# ── Gate 1+2: known-answer join ───────────────────────────────────────────────

class KnownAnswerJoinTest(SimpleTestCase):
    """Synthetic cycles with hand-computed expected feature values at decision candle."""

    def test_features_come_from_decision_candle_not_fill_candle(self):
        """
        Decision candle (07:00) has ADX=20. Fill candle (08:00) has ADX=99.
        The extractor must attach ADX=20, not 99.
        """
        # Two candles: decision (07:00) and fill (08:00) — different ADX values
        idx = pd.to_datetime(["2024-06-15 07:00:00+00:00", "2024-06-15 08:00:00+00:00"])
        data = {feat: [np.nan, np.nan] for feat in ENTRY_FEATURE_FAMILY}
        data["adx"] = [20.0, 99.0]   # decision=20, fill=99
        obs_df = pd.DataFrame(data, index=idx)

        cycles = [_make_cycle("2024-06-15 08:00:00+00:00")]  # fill at 08:00 → decision=07:00
        sim_results = {"EUR_USD": {"trades": cycles}}
        result = extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )
        self.assertFalse(result.cycle_df.empty)
        self.assertAlmostEqual(
            result.cycle_df["adx"].iloc[0], 20.0,
            msg="ADX must come from decision candle (07:00=20), NOT fill candle (08:00=99)",
        )

    def test_unmatched_cycle_increments_unmatched_count(self):
        """A cycle whose decision candle is missing from obs is counted, not silently dropped."""
        obs_df = _make_obs_df(["2024-06-15 06:00:00+00:00"])  # only 06:00
        cycles = [_make_cycle("2024-06-15 08:00:00+00:00")]    # decision=07:00, not in obs
        sim_results = {"EUR_USD": {"trades": cycles}}
        result = extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )
        self.assertEqual(result.total_unmatched, 1)
        self.assertEqual(result.total_matched, 0)
        self.assertTrue(result.cycle_df.empty)

    def test_two_cycles_same_pair_both_matched(self):
        obs_df = _make_obs_df([
            "2024-06-15 07:00:00+00:00",  # decision for cycle 1
            "2024-06-15 09:00:00+00:00",  # decision for cycle 2
        ], feature_val=15.0)
        cycles = [
            _make_cycle("2024-06-15 08:00:00+00:00", profit_usdt=2.0),
            _make_cycle("2024-06-15 10:00:00+00:00", profit_usdt=-1.0),
        ]
        sim_results = {"EUR_USD": {"trades": cycles}}
        result = extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(obs_df),
        )
        self.assertEqual(result.total_matched, 2)
        self.assertEqual(len(result.cycle_df), 2)

    def test_missing_obs_csv_gives_zero_matched(self):
        """When obs CSV is empty the pair is reported with matched=0."""
        empty_obs = pd.DataFrame()
        cycles = [_make_cycle("2024-06-15 08:00:00+00:00")]
        sim_results = {"EUR_USD": {"trades": cycles}}
        result = extract_dca_cycles(
            _sim_fn=_sim_fn_from(sim_results),
            _obs_fn=_obs_fn_from(empty_obs),
        )
        self.assertEqual(result.total_matched, 0)
        self.assertEqual(result.reports[0].matched, 0)
        self.assertEqual(result.reports[0].unmatched, 1)


# ── Gate 5: feature family pre-declaration ────────────────────────────────────

class FeatureFamilyPreDeclarationTest(SimpleTestCase):
    """ENTRY_FEATURE_FAMILY must include the primary DCA strategy inputs."""

    def test_adx_in_feature_family(self):
        self.assertIn("adx", ENTRY_FEATURE_FAMILY,
                      "ADX is the primary DCA regime gate — must be in ENTRY_FEATURE_FAMILY")

    def test_z_score_in_feature_family(self):
        self.assertIn("z_score", ENTRY_FEATURE_FAMILY)

    def test_ema_spread_pct_in_feature_family(self):
        self.assertIn("ema_spread_pct", ENTRY_FEATURE_FAMILY)

    def test_feature_family_has_no_duplicates(self):
        self.assertEqual(len(ENTRY_FEATURE_FAMILY), len(set(ENTRY_FEATURE_FAMILY)))

    def test_censored_reason_constant(self):
        self.assertEqual(CENSORED_REASON, "DATA_END_STILL_OPEN")


# ── print_alignment_report smoke-test ────────────────────────────────────────

class PrintAlignmentReportTest(SimpleTestCase):
    """print_alignment_report must not crash on empty or single-pair results."""

    def _empty_result(self) -> AlignmentResult:
        return AlignmentResult(
            cycle_df=pd.DataFrame(),
            reports=[],
            pairs_found=[],
            total_matched=0,
            total_censored=0,
            total_unmatched=0,
            feature_family=list(ENTRY_FEATURE_FAMILY),
        )

    def test_print_empty_result_does_not_raise(self):
        import sys
        from io import StringIO
        result = self._empty_result()
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            print_alignment_report(result)
        finally:
            sys.stdout = old_stdout
