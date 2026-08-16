import os

import pandas as pd
from django.test import TestCase

# Create your tests here.


# claude code changed: new — fixes architecture audit finding H3.
#
# bot/backtesting/regime_precomputer.py's own module docstring states its
# four indicator formulas are "copied verbatim from RegimeDetector's
# private methods... any drift between this file and RegimeDetector's
# math would silently make backtests disagree with the live bot, so the
# formulas must be copied, not 'improved on,' every time RegimeDetector's
# math changes" — a real, documented risk with nothing enforcing it. This
# test runs both the vectorized precompute path and the per-candle
# detect() path over the same real historical data and asserts every
# RegimeResult field matches exactly. If RegimeDetector's indicator math
# is ever changed without mirroring the change in regime_precomputer.py,
# this test fails instead of the two paths silently disagreeing in
# production (backtests would then no longer represent what live trading
# actually does).
class RegimePrecomputerEquivalenceTest(TestCase):

    def test_precompute_matches_per_candle_detect(self):
        # Import here (not at module scope) so a Django test run doesn't
        # pay the app-loading cost for these on every test file, only
        # when this specific test runs
        from bot.engines.regime_detector import RegimeDetector
        from bot.backtesting.regime_precomputer import precompute_regime_results

        data_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "data", "AAVE_USDT_1h.csv",
        )
        data_path = os.path.normpath(data_path)

        if not os.path.exists(data_path):
            # data/*.csv is populated by fetch_all_symbols.py and isn't
            # guaranteed to exist in every checkout — skip rather than
            # fail the whole suite in an environment that hasn't run it
            self.skipTest(f"No historical data at {data_path} — run fetch_all_symbols.py first")

        df = pd.read_csv(data_path)
        time_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col)

        # Small slice — enough candles to clear every indicator's warmup
        # (longest period used anywhere is 50; RegimeDetector.min_candles=60)
        # while keeping this test fast
        df = df.iloc[-400:].copy()
        for col in ["open", "close", "high", "low"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "high", "low"])

        detector = RegimeDetector()
        precomputed = precompute_regime_results(df, detector)

        self.assertEqual(len(precomputed), len(df))

        # Check every 10th candle after warmup — checking all 400 would be
        # slow (each detect() call reconstructs indicators from scratch)
        # without adding real coverage beyond what a sample already proves.
        # Uses df.iloc[:i+1] (unbounded from candle 0) deliberately — that
        # is exactly the equivalence regime_precomputer.py's own docstring
        # claims ("computing them on the full series and reading row i is
        # identical to computing them on df.iloc[:i+1] and reading the last
        # row"), not the separate 300-candle INDICATOR_LOOKBACK_CANDLES
        # bound Backtester.run() applies for performance — that's a
        # different, already-documented assumption, not what this test
        # is checking.
        mismatches = []
        for i in range(detector.min_candles, len(df), 10):
            live_result = detector.detect(df.iloc[:i + 1])
            precomputed_result = precomputed[i]

            for field in (
                "regime", "confidence", "adx", "atr_ratio",
                "ema_spread_pct", "bb_width_pct",
                "adx_trending", "volatility_extreme",
            ):
                live_val = getattr(live_result, field)
                pre_val = getattr(precomputed_result, field)

                if isinstance(live_val, float):
                    if abs(live_val - pre_val) > 1e-9:
                        mismatches.append((i, field, live_val, pre_val))
                elif live_val != pre_val:
                    mismatches.append((i, field, live_val, pre_val))

        self.assertEqual(
            mismatches, [],
            f"precompute_regime_results() disagreed with RegimeDetector.detect() "
            f"at {len(mismatches)} (candle, field) point(s) — regime_precomputer.py's "
            f"formulas have drifted from RegimeDetector's own methods. "
            f"First few mismatches (candle_index, field, live_value, precomputed_value): "
            f"{mismatches[:5]}"
        )
