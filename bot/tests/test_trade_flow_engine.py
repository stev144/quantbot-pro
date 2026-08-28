# claude code changed: new file — Phase 2B, Steps 6-9. Coverage for
# bot/research/trade_flow_engine.py. Small, deterministic, hand-built
# fixtures throughout (no network, no real trade data needed) — these are
# mathematical/causal-correctness unit tests, per Phase 2B Step 16's
# explicit instruction not to require real network data for them.
#
# The most important test class here is CausalAlignmentTest — this module
# found a REAL bug during construction (a resolution-mismatch in
# candle-timestamp-to-milliseconds conversion that silently matched ZERO
# trades to ANY candle) purely because its own construction included a
# boundary-exact smoke test before this formal suite was written. These
# tests exist specifically to keep that class of bug caught permanently.

import ast
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.instruments import UnsupportedTimeframeError
from bot.research.trade_flow_engine import TradeFlowEngine


def _trades(rows):
    """rows: list of (trade_id, timestamp_str, quantity, is_buyer_maker)."""
    return pd.DataFrame({
        "trade_id": [r[0] for r in rows],
        "timestamp": [pd.Timestamp(r[1], tz="UTC").value // 10**6 for r in rows],
        "price": [100.0] * len(rows),
        "quantity": [r[2] for r in rows],
        "is_buyer_maker": [r[3] for r in rows],
        "symbol": ["BTC/USDT"] * len(rows),
        "source": ["binance"] * len(rows),
    })


class UnsupportedTimeframeTest(SimpleTestCase):

    def test_unknown_timeframe_fails_closed_at_construction(self):
        with self.assertRaises(UnsupportedTimeframeError):
            TradeFlowEngine(timeframe="3m")   # claude code changed: not in TIMEFRAME_MINUTES_PER_CANDLE — must never silently assume 1h


class CausalAlignmentTest(SimpleTestCase):
    """The causal-safety-critical core: a candle at t covers [t, t+interval)
    and must NEVER include a trade at or after t+interval, NEVER exclude a
    trade legitimately inside its own window, and NEVER misattribute a
    trade across a gap in the candle index."""

    def setUp(self):
        self.candle_index = pd.DatetimeIndex(
            ["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 02:00"], tz="UTC"
        )
        self.engine = TradeFlowEngine(timeframe="1h")

    def test_trade_exactly_at_candle_start_belongs_to_that_candle_not_the_previous_one(self):
        trades = _trades([(1, "2024-01-01 01:00:00.000", 1.0, False)])
        owning = self.engine.align_trades_to_candles(trades, self.candle_index)
        self.assertEqual(owning.iloc[0], self.candle_index[1])

    def test_trade_one_millisecond_before_boundary_belongs_to_the_earlier_candle(self):
        trades = _trades([(1, "2024-01-01 00:59:59.999", 1.0, False)])
        owning = self.engine.align_trades_to_candles(trades, self.candle_index)
        self.assertEqual(owning.iloc[0], self.candle_index[0])

    def test_trade_after_the_last_candles_close_is_excluded_not_leaked_backward(self):
        # claude code changed: THE critical no-look-ahead-in-reverse case —
        # a trade after the final candle's [t, t+interval) window must
        # never be silently folded into that last candle.
        trades = _trades([(1, "2024-01-01 03:30:00.000", 999.0, False)])
        owning = self.engine.align_trades_to_candles(trades, self.candle_index)
        self.assertTrue(pd.isna(owning.iloc[0]))

        features = self.engine.compute_features(trades, self.candle_index)
        self.assertEqual(features["buy_volume"].iloc[-1], 0.0)   # claude code changed: the 999.0 must NOT appear anywhere

    def test_trade_before_the_first_candle_is_excluded(self):
        trades = _trades([(1, "2023-12-31 23:00:00.000", 1.0, False)])
        owning = self.engine.align_trades_to_candles(trades, self.candle_index)
        self.assertTrue(pd.isna(owning.iloc[0]))

    def test_trade_inside_a_real_gap_between_non_adjacent_candles_is_excluded_not_misattributed(self):
        # claude code changed: candle_index has a GAP (00:00 then 03:00 —
        # no 01:00/02:00 rows, simulating exchange downtime). A trade at
        # 01:30 must NOT be silently folded into the 00:00 candle just
        # because it's the nearest preceding one — that candle's real
        # window is only [00:00, 01:00).
        gapped_index = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 03:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 01:30:00.000", 5.0, False)])
        owning = self.engine.align_trades_to_candles(trades, gapped_index)
        self.assertTrue(pd.isna(owning.iloc[0]))

    def test_reproducibility_same_inputs_same_output(self):
        trades = _trades([
            (1, "2024-01-01 00:10", 1.0, False), (2, "2024-01-01 00:40", 2.0, True),
            (3, "2024-01-01 01:20", 3.0, False),
        ])
        result_1 = self.engine.compute_features(trades, self.candle_index)
        result_2 = self.engine.compute_features(trades, self.candle_index)
        pd.testing.assert_frame_equal(result_1, result_2)


class AggressorDirectionSemanticsTest(SimpleTestCase):
    """is_buyer_maker=True -> SELL-initiated (seller crossed the spread);
    False -> BUY-initiated. Getting this backwards silently corrupts every
    downstream signed-volume calculation without ever raising an error —
    see trade_data.py's module docstring for the full derivation."""

    def test_buyer_maker_true_counts_as_sell_volume(self):
        engine = TradeFlowEngine(timeframe="1h")
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 00:10", 7.0, True)])   # is_buyer_maker=True
        features = engine.compute_features(trades, candle_index)
        self.assertEqual(features["sell_volume"].iloc[0], 7.0)
        self.assertEqual(features["buy_volume"].iloc[0], 0.0)

    def test_buyer_maker_false_counts_as_buy_volume(self):
        engine = TradeFlowEngine(timeframe="1h")
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 00:10", 7.0, False)])   # is_buyer_maker=False
        features = engine.compute_features(trades, candle_index)
        self.assertEqual(features["buy_volume"].iloc[0], 7.0)
        self.assertEqual(features["sell_volume"].iloc[0], 0.0)


class DeltaCvdTest(SimpleTestCase):

    def test_delta_is_buy_minus_sell(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 00:10", 10.0, False), (2, "2024-01-01 00:20", 3.0, True)])
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        self.assertEqual(features["delta"].iloc[0], 7.0)   # 10 buy - 3 sell

    def test_cvd_accumulates_across_candles_including_zero_trade_candles(self):
        # claude code changed: candle 1 (index 1) has ZERO trades — CVD must
        # carry the running total through unchanged, not reset or NaN.
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 02:00"], tz="UTC")
        trades = _trades([
            (1, "2024-01-01 00:10", 5.0, False),    # candle 0: delta=+5
            (2, "2024-01-01 02:10", 2.0, True),      # candle 2: delta=-2, candle 1 has NO trades
        ])
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        self.assertEqual(features["cvd"].tolist(), [5.0, 5.0, 3.0])   # 5, carried through zero-trade candle 1, then 5-2=3

    def test_cvd_reset_convention_is_caller_controlled_by_input_slice(self):
        # claude code changed: proves the documented convention — CVD
        # restarts from zero for whatever slice of candle_index is passed,
        # not some hidden engine-internal reset schedule.
        candle_index_day1 = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00"], tz="UTC")
        candle_index_day2 = pd.DatetimeIndex(["2024-01-02 00:00", "2024-01-02 01:00"], tz="UTC")
        trades_day2 = _trades([(1, "2024-01-02 00:10", 4.0, False)])
        features_day2 = TradeFlowEngine(timeframe="1h").compute_features(trades_day2, candle_index_day2)
        self.assertEqual(features_day2["cvd"].iloc[0], 4.0)   # claude code changed: starts fresh at 4.0, unaffected by any "day1" concept


class SafeRatioHandlingTest(SimpleTestCase):

    def test_buy_sell_ratio_is_nan_not_inf_when_no_sell_volume(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 00:10", 5.0, False)])   # all buy, zero sell
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        self.assertTrue(np.isnan(features["buy_sell_ratio"].iloc[0]))

    def test_avg_trade_size_is_nan_not_zero_for_a_zero_trade_candle(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([])   # claude code changed: empty trades DataFrame
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        self.assertEqual(features["trade_intensity"].iloc[0], 0)
        self.assertTrue(np.isnan(features["avg_trade_size"].iloc[0]))

    def test_trade_intensity_counts_trades_not_volume(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 00:10", 100.0, False), (2, "2024-01-01 00:20", 0.001, True)])
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        self.assertEqual(features["trade_intensity"].iloc[0], 2)   # claude code changed: 2 trades, not influenced by the huge size disparity


class DivergenceIsResearchFeatureNotSignalTest(SimpleTestCase):

    def test_divergence_flags_opposite_signed_price_and_flow(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00"], tz="UTC")
        trades = _trades([(1, "2024-01-01 01:10", 5.0, True)])   # candle 1: sell-heavy, delta negative
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        close = pd.Series([100.0, 105.0], index=candle_index)   # price UP while flow is DOWN (delta<0 at candle 1)
        out = TradeFlowEngine(timeframe="1h").compute_divergence(features, close)
        self.assertEqual(out["price_flow_divergence"].iloc[1], True)

    def test_divergence_is_nan_not_false_when_either_side_is_flat(self):
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00"], tz="UTC")
        trades = _trades([])
        features = TradeFlowEngine(timeframe="1h").compute_features(trades, candle_index)
        close = pd.Series([100.0, 100.0], index=candle_index)   # claude code changed: flat price, and zero flow -> "cannot say", not "no divergence"
        out = TradeFlowEngine(timeframe="1h").compute_divergence(features, close)
        self.assertTrue(pd.isna(out["price_flow_divergence"].iloc[1]))


class MultiSymbolBehaviorTest(SimpleTestCase):

    def test_engine_processes_each_symbols_trades_independently(self):
        # claude code changed: proves no cross-symbol state leaks between
        # two separate compute_features() calls on the same engine instance.
        candle_index = pd.DatetimeIndex(["2024-01-01 00:00"], tz="UTC")
        engine = TradeFlowEngine(timeframe="1h")

        btc_trades = pd.DataFrame({
            "trade_id": [1], "timestamp": [pd.Timestamp("2024-01-01 00:10", tz="UTC").value // 10**6],
            "price": [100.0], "quantity": [5.0], "is_buyer_maker": [False],
            "symbol": ["BTC/USDT"], "source": ["binance"],
        })
        eth_trades = pd.DataFrame({
            "trade_id": [1], "timestamp": [pd.Timestamp("2024-01-01 00:10", tz="UTC").value // 10**6],
            "price": [50.0], "quantity": [9.0], "is_buyer_maker": [True],
            "symbol": ["ETH/USDT"], "source": ["binance"],
        })

        btc_features = engine.compute_features(btc_trades, candle_index)
        eth_features = engine.compute_features(eth_trades, candle_index)

        self.assertEqual(btc_features["buy_volume"].iloc[0], 5.0)
        self.assertEqual(eth_features["sell_volume"].iloc[0], 9.0)
        self.assertEqual(btc_features["sell_volume"].iloc[0], 0.0)   # claude code changed: BTC's own sell_volume unaffected by ETH's separate call


class ChunkedProcessingSafetyTest(SimpleTestCase):
    """claude code changed: new — Phase 2D (multi-month scaling requires
    processing trades day-by-day rather than holding the full raw history
    in memory at once, since e.g. 6 months of BTC/USDT aggTrades is
    ~100M+ rows). Proves, using ONLY the existing, already-committed
    TradeFlowEngine methods (no new production code needed for this
    proof), exactly which columns are safe to compute per-day-chunk and
    which are NOT — the property Phase 2D's acquisition script relies on.
    Per-candle aggregations (buy_volume, sell_volume, delta, trade_intensity,
    avg_trade_size, buy_sell_ratio) depend ONLY on trades within that one
    candle's own window, so chunking by day cannot change them. cvd (a
    running cumulative sum) and anything derived from it MUST be
    recomputed on the full concatenated series after chunked processing,
    or it silently resets at every chunk boundary."""

    def _two_day_trades(self):
        # claude code changed: trades spanning a day boundary — day 1 has
        # 2 candles (22:00, 23:00), day 2 has 2 candles (00:00, 01:00) of
        # the NEXT day, deliberately with nonzero delta in every candle so
        # a day-boundary CVD reset is numerically distinguishable from the
        # correct continuous value.
        rows = [
            (1, "2024-01-01 22:10", 5.0, False),   # day 1, candle 22:00, buy
            (2, "2024-01-01 23:10", 2.0, True),    # day 1, candle 23:00, sell
            (3, "2024-01-02 00:10", 4.0, False),   # day 2, candle 00:00, buy
            (4, "2024-01-02 01:10", 1.0, True),    # day 2, candle 01:00, sell
        ]
        return pd.DataFrame({
            "trade_id": [r[0] for r in rows],
            "timestamp": [pd.Timestamp(r[1], tz="UTC").value // 10**6 for r in rows],
            "price": [100.0] * len(rows),
            "quantity": [r[2] for r in rows],
            "is_buyer_maker": [r[3] for r in rows],
            "symbol": ["BTC/USDT"] * len(rows),
            "source": ["binance"] * len(rows),
        })

    def test_per_candle_columns_are_identical_whether_chunked_by_day_or_computed_in_one_call(self):
        trades = self._two_day_trades()
        full_candle_index = pd.DatetimeIndex(
            ["2024-01-01 22:00", "2024-01-01 23:00", "2024-01-02 00:00", "2024-01-02 01:00"], tz="UTC"
        )
        engine = TradeFlowEngine(timeframe="1h")

        # ── Single, non-chunked call (the "ground truth") ──────────────
        single_call = engine.compute_features(trades, full_candle_index)

        # ── Chunked: one compute_features() call per UTC day ───────────
        day1_index = full_candle_index[full_candle_index < pd.Timestamp("2024-01-02", tz="UTC")]
        day2_index = full_candle_index[full_candle_index >= pd.Timestamp("2024-01-02", tz="UTC")]
        day1_chunk = engine.compute_features(trades, day1_index)
        day2_chunk = engine.compute_features(trades, day2_index)   # claude code changed: full trades_df passed each time — align_trades_to_candles() itself filters to only the trades that actually fall in that chunk's window, exactly like a real per-day archive file would only ever contain that day's trades
        chunked = pd.concat([day1_chunk, day2_chunk])

        # claude code changed: THE property this whole chunking strategy
        # depends on — per-candle columns must be byte-identical whether
        # computed in one call or two, since each candle's own value never
        # depends on any other candle.
        for col in ("buy_volume", "sell_volume", "delta", "trade_intensity", "avg_trade_size", "buy_sell_ratio"):
            pd.testing.assert_series_equal(
                single_call[col].reset_index(drop=True), chunked[col].reset_index(drop=True),
                check_names=False, obj=f"chunked vs single-call must match for '{col}'",
            )

    def test_cvd_silently_resets_at_chunk_boundary_unless_recomputed_on_the_full_series(self):
        # claude code changed: THE negative proof — demonstrates exactly
        # the bug Phase 2D's acquisition script must avoid. Naively
        # concatenating each day's own "cvd" column (each restarting at 0,
        # per compute_features' own documented convention) produces a
        # WRONG result at the day-2 candles compared to the true
        # continuous cumulative sum.
        trades = self._two_day_trades()
        full_candle_index = pd.DatetimeIndex(
            ["2024-01-01 22:00", "2024-01-01 23:00", "2024-01-02 00:00", "2024-01-02 01:00"], tz="UTC"
        )
        engine = TradeFlowEngine(timeframe="1h")

        single_call = engine.compute_features(trades, full_candle_index)
        true_cvd = single_call["cvd"].reset_index(drop=True)

        day1_index = full_candle_index[full_candle_index < pd.Timestamp("2024-01-02", tz="UTC")]
        day2_index = full_candle_index[full_candle_index >= pd.Timestamp("2024-01-02", tz="UTC")]
        day1_chunk = engine.compute_features(trades, day1_index)
        day2_chunk = engine.compute_features(trades, day2_index)
        naive_chunked_cvd = pd.concat([day1_chunk["cvd"], day2_chunk["cvd"]]).reset_index(drop=True)

        # claude code changed: the naive per-chunk cvd is WRONG from candle
        # 2 (day 2's first candle) onward — proves recomputation is required,
        # not optional.
        self.assertFalse(true_cvd.equals(naive_chunked_cvd), "if this now passes, compute_features' CVD convention changed and Phase 2D's acquisition script assumption must be re-verified")

        # ── The FIX: recompute cvd on the concatenated delta series ─────
        chunked_delta = pd.concat([day1_chunk["delta"], day2_chunk["delta"]]).reset_index(drop=True)
        fixed_cvd = chunked_delta.cumsum()
        pd.testing.assert_series_equal(true_cvd, fixed_cvd, check_names=False, obj="recomputing cvd from the concatenated delta series must match the true continuous value")


class SecurityBoundaryTest(SimpleTestCase):
    """Phase 2B, Step 10: trade-data ingestion and the trade-flow research
    engine must remain completely outside order placement, execution,
    credentials, wallet management, billing, and AI/LLM execution logic.
    ast-based import scan — same approach used for entry_exit_engine.py
    (Phase 1C) and the Kalman Research Lab tool (Phase 1D); a bare-substring
    grep would false-positive on this module's own architecture comments
    (e.g. trade_data.py's docstring explicitly discusses NOT being an
    ExchangeAdapter method)."""

    FORBIDDEN_MODULE_SUBSTRINGS = ["execution_engine", "order_manager", "bot_runner"]
    FORBIDDEN_CREDENTIAL_TOKENS = ["API_KEY", "API_SECRET", "api_key", "api_secret", "wallet"]

    def _imported_modules(self, module):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        return imported

    def test_no_execution_or_order_placement_imports(self):
        import bot.engines.trade_data as trade_data_module
        import bot.research.trade_flow_engine as trade_flow_module

        for module in (trade_data_module, trade_flow_module):
            for imported in self._imported_modules(module):
                for forbidden in self.FORBIDDEN_MODULE_SUBSTRINGS:
                    self.assertNotIn(
                        forbidden, imported,
                        f"{module.__name__} imports '{imported}', referencing '{forbidden}' — "
                        f"a real path toward live execution, not just market data."
                    )

    def test_no_hardcoded_credential_tokens(self):
        for path in (
            Path("bot/engines/trade_data.py"),
            Path("bot/research/trade_flow_engine.py"),
        ):
            source = path.read_text(encoding="utf-8")
            for token in self.FORBIDDEN_CREDENTIAL_TOKENS:
                self.assertNotIn(token, source, f"{path} unexpectedly references '{token}'")

    def test_trade_data_only_calls_public_ccxt_endpoints(self):
        # claude code changed: aggTrades is a PUBLIC endpoint (no API key
        # required) — confirm no ccxt private/signed method (which would
        # require credentials) is referenced anywhere.
        source = Path("bot/engines/trade_data.py").read_text(encoding="utf-8")
        self.assertNotIn("apiKey", source)
        self.assertNotIn("secret", source.lower())
