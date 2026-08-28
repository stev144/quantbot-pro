# claude code changed: new file — Phase 2F Step 3 (causality/data-integrity
# gate for the order-book/L2 microstructure engine), ahead of any
# statistical testing. Reuses this project's existing conventions exactly:
# deterministic synthetic timestamps for leakage proofs (same technique as
# test_derivatives_engine.py's MergeFundingNoLookaheadTest /
# OpenInterestNoLookaheadTest), plus one real-network end-to-end test
# (no mocking, per this project's established convention).
#
# Two of Step 3's checklist items ("crossed/invalid books", "spread cannot
# become negative") do not literally apply to this data shape — bookDepth
# ships Binance's own pre-aggregated cumulative depth/notional per
# percentage band, not explicit bid/ask price levels, so there is no
# "best bid >= best ask" or "spread" to check. The applicable analogous
# integrity property for THIS shape is monotonicity: cumulative
# depth/notional at a wider band can never be less than at a narrower band
# on the same side (each wider band's cumulative sum strictly contains the
# narrower one) — tested below against real data.

import time

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.orderbook_depth_engine import (
    DEPTH_COLUMNS,
    OrderBookDepthEngine,
    fetch_book_depth_archive,
)


def _make_candle_index(n, freq="1h", start="2024-01-01"):
    return pd.date_range(start, periods=n, freq=freq, tz="UTC")


def _synthetic_snapshot_rows(ts, bid_notional_by_band, ask_notional_by_band):
    rows = []
    for band, notional in bid_notional_by_band.items():
        rows.append({"timestamp": ts, "percentage": -band, "depth": notional / 100.0, "notional": notional})
    for band, notional in ask_notional_by_band.items():
        rows.append({"timestamp": ts, "percentage": band, "depth": notional / 100.0, "notional": notional})
    return rows


class DepthImbalanceAndConcentrationNoLookaheadTest(SimpleTestCase):
    """claude code changed: new — the future-leakage proof for this engine,
    same deterministic-synthetic-timestamp / mutation-invariance technique
    already established for funding/OI (test_derivatives_engine.py)."""

    def _two_snapshot_series(self, n_candles=6):
        """Snapshots every 30 minutes (bookDepth's real ~30s cadence is
        finer than needed for a unit test — 30min is enough to prove
        candle-level causal alignment) — candle 0 (00:00) only sees the
        snapshot at 23:30 of the "previous" period; deliberately offset so
        no snapshot lands exactly on a candle boundary."""
        candle_index = _make_candle_index(n_candles)
        snapshot_times = pd.date_range("2023-12-31 23:45", periods=n_candles + 1, freq="1h", tz="UTC")

        rows = []
        for i, ts in enumerate(snapshot_times):
            rows.extend(_synthetic_snapshot_rows(
                ts,
                bid_notional_by_band={0.2: 100 + i, 2.0: 500 + i * 10, 5.0: 1000 + i * 20},
                ask_notional_by_band={0.2: 100 + i, 2.0: 400 + i * 5, 5.0: 900 + i * 15},
            ))
        depth_df = pd.DataFrame(rows, columns=DEPTH_COLUMNS)
        return depth_df, candle_index, snapshot_times

    def test_each_candle_only_sees_the_most_recent_prior_snapshot(self):
        depth_df, candle_index, snapshot_times = self._two_snapshot_series()
        engine = OrderBookDepthEngine()
        result = engine.compute_features(depth_df, candle_index)

        # claude code changed: candle 0 (2024-01-01 00:00) — the most recent
        # snapshot with timestamp <= it is snapshot index 0 (2023-12-31 23:45),
        # NOT snapshot index 1 (2024-01-01 00:45, which is AFTER candle 0).
        expected_bid_2 = 500 + 0 * 10
        expected_ask_2 = 400 + 0 * 5
        expected_imbalance = (expected_bid_2 - expected_ask_2) / (expected_bid_2 + expected_ask_2)
        self.assertAlmostEqual(result.loc[0, "depth_imbalance_2pct"], expected_imbalance, places=10)

    def test_future_snapshot_cannot_change_an_earlier_candles_feature(self):
        """claude code changed: mutation-invariance proof — perturbing a
        LATER snapshot must never change an EARLIER candle's feature value,
        and (so the test isn't vacuous) must change something at/after it."""
        depth_df, candle_index, snapshot_times = self._two_snapshot_series(n_candles=8)
        engine = OrderBookDepthEngine()
        original = engine.compute_features(depth_df, candle_index)

        # claude code changed: mutate ONLY the bid side of the 2% band —
        # scaling BOTH bid and ask notional by the same factor would leave
        # depth_imbalance_2pct (a ratio) unchanged, making the "not vacuous"
        # check below meaningless. An asymmetric mutation is the real test
        # of whether the merge actually incorporates this specific snapshot.
        mutate_ts = snapshot_times[4]
        mutated_df = depth_df.copy()
        mutate_mask = (mutated_df["timestamp"] == mutate_ts) & (mutated_df["percentage"] == -2.0)
        mutated_df.loc[mutate_mask, "notional"] *= 50.0
        mutated = engine.compute_features(mutated_df, candle_index)

        before_mask = candle_index < mutate_ts
        pd.testing.assert_frame_equal(
            original.loc[before_mask].reset_index(drop=True),
            mutated.loc[before_mask].reset_index(drop=True),
        )
        after_mask = ~before_mask
        self.assertFalse(
            original.loc[after_mask, "depth_imbalance_2pct"].reset_index(drop=True).equals(
                mutated.loc[after_mask, "depth_imbalance_2pct"].reset_index(drop=True)
            )
        )

    def test_candles_before_the_first_snapshot_are_nan_not_backfilled(self):
        depth_df, candle_index, snapshot_times = self._two_snapshot_series()
        # claude code changed: shift every real snapshot to AFTER the whole
        # candle window — proves a candle with no prior snapshot at all
        # gets NaN, never fabricated from a future-relative-to-it snapshot.
        shifted = depth_df.copy()
        shifted["timestamp"] = shifted["timestamp"] + pd.Timedelta(days=10)

        engine = OrderBookDepthEngine()
        result = engine.compute_features(shifted, candle_index)
        self.assertTrue(result["depth_imbalance_2pct"].isna().all())
        self.assertTrue(result["depth_concentration"].isna().all())

    def test_empty_depth_df_produces_nan_not_a_crash(self):
        candle_index = _make_candle_index(5)
        empty = pd.DataFrame(columns=DEPTH_COLUMNS)
        engine = OrderBookDepthEngine()
        result = engine.compute_features(empty, candle_index)
        self.assertTrue(result["depth_imbalance_2pct"].isna().all())
        self.assertTrue(result["depth_concentration"].isna().all())
        self.assertEqual(len(result), 5)

    def test_invalid_band_choice_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            OrderBookDepthEngine(imbalance_band_pct=1.5)   # not a real bookDepth band

    def test_mismatched_datetime64_resolutions_merge_without_raising(self):
        """claude code changed: regression test — real bug found during
        Phase 2F's first end-to-end run on live data. OHLCV-derived
        candle indexes are typically datetime64[ms] (pd.to_datetime(...,
        unit="ms")); this archive's own timestamp strings parse to
        datetime64[us] by default. pandas 3.x's merge_asof raises
        ("incompatible merge keys") rather than silently upcasting when
        the two sides' resolutions differ — this test fixture
        deliberately constructs them at DIFFERENT resolutions (unlike
        every other test in this file, which happened to construct both
        sides at the same resolution and so never caught this)."""
        depth_df, candle_index, snapshot_times = self._two_snapshot_series(n_candles=6)

        # Force a resolution mismatch: candle_index at ms, depth_df at us —
        # exactly the real-world combination that crashed Phase 2F.
        candle_index = candle_index.as_unit("ms")
        depth_df = depth_df.copy()
        depth_df["timestamp"] = depth_df["timestamp"].dt.as_unit("us")
        self.assertNotEqual(candle_index.dtype, depth_df["timestamp"].dtype)

        engine = OrderBookDepthEngine()
        result = engine.compute_features(depth_df, candle_index)   # must not raise

        expected_bid_2 = 500 + 0 * 10
        expected_ask_2 = 400 + 0 * 5
        expected_imbalance = (expected_bid_2 - expected_ask_2) / (expected_bid_2 + expected_ask_2)
        self.assertAlmostEqual(result.loc[0, "depth_imbalance_2pct"], expected_imbalance, places=10)


class RealDataIntegrityTest(SimpleTestCase):
    """claude code changed: new — real network call (no mocking, per this
    project's convention), verifying against Binance's actual archive
    rather than only a synthetic fixture."""

    def test_real_btc_bookdepth_day_has_monotonic_cumulative_bands_and_no_negative_values(self):
        """claude code changed: THE applicable analogous check to
        "crossed/invalid book" for this data shape — see module docstring.
        A wider percentage band's cumulative notional must be >= a
        narrower band's, on the same side, at the same snapshot; and no
        depth/notional value may be negative."""
        yesterday = (pd.Timestamp.utcnow() - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        df = fetch_book_depth_archive("BTC/USDT", yesterday)
        if df.empty:
            self.skipTest("archive file not available for this date in this environment")

        self.assertTrue((df["depth"] >= 0).all())
        self.assertTrue((df["notional"] >= 0).all())

        wide = df.pivot_table(index="timestamp", columns="percentage", values="notional", aggfunc="first")
        bid_bands = sorted([c for c in wide.columns if c < 0], reverse=True)   # -0.2, -1.0, ... -5.0
        ask_bands = sorted([c for c in wide.columns if c > 0])                 # 0.2, 1.0, ... 5.0

        for bands in (bid_bands, ask_bands):
            for narrower, wider in zip(bands, bands[1:]):
                self.assertTrue((wide[wider] >= wide[narrower]).all(),
                                 f"band {wider} must be >= band {narrower} (cumulative from mid) on every snapshot")

    def test_real_fetch_is_read_only_public_data_no_credentials(self):
        """claude code changed: security-boundary sanity check, mirroring
        test_trade_flow_engine.py's SecurityBoundaryTest precedent for the
        other archive-based engine — this is a plain unauthenticated GET,
        no path toward order placement or credentials."""
        import inspect
        source = inspect.getsource(fetch_book_depth_archive)
        for forbidden in ("api_key", "secret", "sign(", "private"):
            self.assertNotIn(forbidden, source.lower())
