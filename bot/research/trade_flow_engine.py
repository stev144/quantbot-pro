# ============================================================
# bot/research/trade_flow_engine.py
# claude code changed: new file — Phase 2B, Steps 6-9. The first
# candle-aligned trade-flow feature layer: consumes bot/engines/
# trade_data.py's raw aggregated trades (source of truth) and produces
# reproducible, candle-aligned derived features. Never stores only the
# derived features and throws away the raw trades — every number here
# must be exactly reconstructable from the raw trade CSV plus this module.
#
# ── EVENT-TIME / DATA-LINEAGE RULES (Phase 2B, Step 3) ────────────────
# Six distinct timestamps matter for this module; conflating any two of
# them is exactly how look-ahead bias enters a research pipeline:
#   1. Event timestamp    — trade_data.py's `timestamp` column (Binance's
#                            `T` field): when the trade actually executed
#                            on the exchange. This is the ONLY timestamp
#                            used to decide which candle a trade belongs
#                            to (see align_trades_to_candles below).
#   2. Exchange timestamp — same as event timestamp for this source
#                            (Binance's own clock, not a third-party
#                            relay) — called out separately only because
#                            a future cross-exchange source (Phase 2E)
#                            would NOT have this property.
#   3. Ingestion timestamp — when THIS PLATFORM fetched the trade (not
#                            currently persisted — see trade_data.py's
#                            module docstring: "ingestion timestamp if
#                            useful for diagnostics" is explicitly
#                            optional, and nothing in this module needs
#                            it, since aggTrades has no publication delay
#                            distinct from event time).
#   4. Publication timestamp — N/A for this source. Called out because it
#                            is NOT always N/A for other Phase-2-roadmap
#                            sources (COT reports, options data) — see
#                            Phase 2A's leakage audit. aggTrades are
#                            streamed/queryable essentially at execution
#                            time, so event time IS publication time here.
#   5. Candle timestamp   — the OHLCV row's own index value, t. By this
#                            platform's existing, established convention
#                            (feature_calculator.py, derivatives_engine.py)
#                            a candle at index t describes what happened
#                            during [t, t+interval), and is only "known"
#                            once that interval has closed.
#   6. Feature evaluation timestamp — the moment a trade-flow feature for
#                            candle t is considered "available" for
#                            research use. Per the rule below, this is
#                            t + interval (the candle's own close) — the
#                            SAME rule every OHLCV-derived feature in this
#                            codebase already follows, not a new one.
#
# THE RULE THIS MODULE ENFORCES (Phase 2B Step 3's mandatory rule):
#   A feature evaluated at time t may only use information that was
#   actually available by t. For a candle-aligned trade-flow feature,
#   that means: candle t's feature value uses ONLY trades with event
#   timestamp in [t, t+interval) — never a trade at or after t+interval
#   (that trade belongs to a LATER candle and is not yet known as of this
#   candle's own close), and never a trade before t (that belongs to an
#   EARLIER candle; forward-filling it into candle t would be using stale
#   information as if it were current, which Step 3 explicitly forbids
#   without a documented reason — no such reason applies here).
#   align_trades_to_candles() uses a gap-safe pd.IntervalIndex (closed on
#   the left, open on the right — i.e. exactly [t, t+interval)) rather
#   than assuming candles are contiguous, so a real gap in the OHLCV
#   history (exchange downtime) correctly excludes any trade that would
#   otherwise be silently misattributed to the wrong candle, rather than
#   guessing.
# ============================================================

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from bot.instruments import TIMEFRAME_MINUTES_PER_CANDLE, UnsupportedTimeframeError

logger = logging.getLogger(__name__)

# claude code changed: new — explicit units, per Phase 2B Step 8's
# "any rolling window, block size, decay period, or lookback must have an
# explicit unit" instruction. This window is CANDLE-COUNT based (not
# wall-clock, not event-count, not session/calendar) — matches the same
# unit convention kalman_filter_engine.py's ZSCORE_WINDOW already uses for
# a 1h-candle dataset. Not silently converted to physical time; a caller
# on a different timeframe gets a different physical duration for the
# same candle count, exactly as every other candle-count window in this
# codebase already behaves (see bot/instruments.py's candles_to_wall_clock
# for the honest conversion if a caller needs the physical duration).
FLOW_ZSCORE_WINDOW_CANDLES = 168     # 1 week of 1h candles — same order of magnitude as kalman_filter_engine.py's WARMUP_CANDLES
FLOW_ZSCORE_MIN_PERIODS_CANDLES = 24  # 1 day minimum before a z-score is trusted


class TradeFlowEngine:
    """
    Aggregates raw trade-level data (bot.engines.trade_data's
    TRADE_COLUMNS shape) into candle-aligned trade-flow features.

    This is a MATHEMATICAL/RESEARCH engine only — see module docstring's
    layer discussion. It does not implement a trading strategy, does not
    generate entry/exit signals, and does not import anything from the
    execution stack (verified by this project's existing ast-based
    security tests, extended for this module in
    bot/tests/test_trade_flow_engine.py).
    """

    def __init__(self, timeframe: str = "1h") -> None:
        if timeframe not in TIMEFRAME_MINUTES_PER_CANDLE:
            raise UnsupportedTimeframeError(
                f"'{timeframe}' has no known candle width — TradeFlowEngine cannot "
                f"align trades to candles without one. Never silently assumes 1h."
            )
        self.timeframe = timeframe
        self.interval_ms = int(TIMEFRAME_MINUTES_PER_CANDLE[timeframe] * 60 * 1000)

    # ─────────────────────────────────────────────────────────────────
    # CANDLE ALIGNMENT — the causal-safety-critical step
    # ─────────────────────────────────────────────────────────────────

    def align_trades_to_candles(self, trades_df: pd.DataFrame, candle_index: pd.DatetimeIndex) -> pd.Series:
        """
        Returns a Series, same length as trades_df, giving each trade's
        owning candle timestamp (as a pd.Timestamp) — or pd.NaT for a
        trade that falls before the first candle, at/after the last
        candle's close, or inside a genuine gap between two non-adjacent
        candles (see module docstring: never guess an attribution).

        Uses each candle's real, explicit [t, t+interval) window rather
        than assuming candle_index is contiguous — a real gap in the
        OHLCV history correctly produces NaT for any trade inside it,
        instead of silently attributing that trade to whichever candle
        happens to precede the gap. (Implemented via np.searchsorted +
        an explicit end-boundary check, not pd.IntervalIndex.get_indexer —
        the latter raises InvalidIndexError on adjacent/touching
        intervals, which is exactly this method's normal case for
        contiguous candles; searchsorted has no such restriction and is
        the more direct tool for a sorted, one-dimensional lookup anyway.)
        """
        if trades_df.empty:
            return pd.Series([], dtype=candle_index.dtype)

        # claude code changed: was candle_index.view("int64") // 1_000_000,
        # which silently assumed datetime64[ns] storage — pandas 3.x's
        # actual default tz-aware resolution is datetime64[us] (confirmed:
        # a naive .view("int64") on that dtype returned SECONDS after the
        # //1_000_000 division, an off-by-1000x bug that made every trade
        # fail to match any candle, caught by this module's own smoke test
        # before it ever reached the test suite). Timedelta-division is
        # resolution-agnostic — correct regardless of pandas' internal
        # storage unit, today or after a future pandas upgrade.
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        starts_ms = ((candle_index - epoch) / pd.Timedelta(milliseconds=1)).to_numpy().astype("int64")
        ends_ms = starts_ms + self.interval_ms
        trade_ts_ms = trades_df["timestamp"].to_numpy()

        # claude code changed: side="right"-1 finds the LAST candle whose
        # start is <= the trade's timestamp — the correct candidate for a
        # closed-left [start, end) window, including the exact-boundary
        # case (a trade timestamped exactly at a candle's start belongs to
        # THAT candle, never the previous one).
        candidate_pos = np.searchsorted(starts_ms, trade_ts_ms, side="right") - 1
        in_bounds = (candidate_pos >= 0) & (candidate_pos < len(starts_ms))

        within_end = np.zeros(len(trade_ts_ms), dtype=bool)
        within_end[in_bounds] = trade_ts_ms[in_bounds] < ends_ms[candidate_pos[in_bounds]]
        matched = in_bounds & within_end

        # claude code changed: dtype derived from candle_index itself
        # (candle_index.dtype), not hardcoded "datetime64[ns, UTC]" — the
        # same off-by-resolution class of bug fixed above; hardcoding a
        # unit here would silently mismatch a caller on a different
        # pandas datetime64 resolution again.
        owning_candle = pd.Series(pd.NaT, index=trades_df.index, dtype=candle_index.dtype)
        owning_candle.loc[matched] = candle_index[candidate_pos[matched]]
        return owning_candle

    # ─────────────────────────────────────────────────────────────────
    # PUBLIC: compute_features()
    # ─────────────────────────────────────────────────────────────────

    def compute_features(self, trades_df: pd.DataFrame, candle_index: pd.DatetimeIndex) -> pd.DataFrame:
        """
        Produces one row per candle in `candle_index`, with columns:
            buy_volume, sell_volume, delta, cvd, buy_sell_ratio,
            trade_intensity, avg_trade_size, flow_acceleration

        A candle with zero trades gets buy_volume=sell_volume=delta=0.0,
        trade_intensity=0, avg_trade_size=NaN (0/0 is undefined, not 0 —
        see _safe_ratio), buy_sell_ratio=NaN. CVD still accumulates
        correctly through a zero-trade candle (delta=0 contributes
        nothing to the running sum, which is mathematically correct, not
        a special case).
        """
        owning_candle = self.align_trades_to_candles(trades_df, candle_index)

        if trades_df.empty:
            grouped_buy = pd.Series(dtype="float64")
            grouped_sell = pd.Series(dtype="float64")
            grouped_count = pd.Series(dtype="int64")
        else:
            work = trades_df.copy()
            work["_candle"] = owning_candle
            work = work.dropna(subset=["_candle"])   # claude code changed: drop unattributed trades (out-of-range or gap) rather than guessing

            # claude code changed: is_buyer_maker=True -> SELL-initiated (buyer
            # was the resting/maker order, seller crossed the spread); False ->
            # BUY-initiated. Getting this backwards is exactly the kind of
            # silent corruption the module docstring warns about — see
            # bot/engines/trade_data.py's own docstring for the full derivation,
            # and bot/tests/test_trade_flow_engine.py's
            # AggressorDirectionSemanticsTest for the regression test.
            is_sell = work["is_buyer_maker"]
            buy_notional = work["quantity"].where(~is_sell, 0.0)
            sell_notional = work["quantity"].where(is_sell, 0.0)

            grouped_buy = buy_notional.groupby(work["_candle"]).sum()
            grouped_sell = sell_notional.groupby(work["_candle"]).sum()
            grouped_count = work.groupby("_candle").size()

        result = pd.DataFrame(index=candle_index)
        result["buy_volume"] = grouped_buy.reindex(candle_index, fill_value=0.0)
        result["sell_volume"] = grouped_sell.reindex(candle_index, fill_value=0.0)
        result["trade_intensity"] = grouped_count.reindex(candle_index, fill_value=0).astype("int64")

        # ── Delta: net aggressive buying minus aggressive selling ──────
        result["delta"] = result["buy_volume"] - result["sell_volume"]

        # ── CVD: cumulative signed volume ───────────────────────────────
        # claude code changed: reset/window convention, explicit per Step 6
        # point 3's requirement — CVD here is cumulative from the FIRST row
        # of whatever candle_index the caller provides, never reset within
        # a call. A caller wanting a "rolling" or "session" CVD controls
        # that by choosing what slice of candle_index/trades_df to pass in
        # (e.g. one call per UTC day for a daily-reset CVD) — this engine
        # does not invent an implicit reset point of its own, since a
        # silent reset choice baked into the engine would be exactly the
        # kind of undocumented convention Step 6 warns against.
        result["cvd"] = result["delta"].cumsum()

        # ── Buy/sell ratio — safe zero-denominator handling ─────────────
        result["buy_sell_ratio"] = _safe_ratio(result["buy_volume"], result["sell_volume"])

        # ── Average trade size — safe zero-count handling ───────────────
        total_volume = result["buy_volume"] + result["sell_volume"]
        result["avg_trade_size"] = _safe_ratio(total_volume, result["trade_intensity"].astype("float64"))

        # ── Flow acceleration — change in delta candle-over-candle ──────
        # claude code changed: causally safe by construction — comparing
        # candle t's already-realized delta to candle t-1's already-realized
        # delta uses no information from after candle t's own close.
        result["flow_acceleration"] = result["delta"].diff()

        return result

    # ─────────────────────────────────────────────────────────────────
    # PRICE/FLOW DIVERGENCE — explicitly a research feature, not a signal
    # ─────────────────────────────────────────────────────────────────

    def compute_divergence(self, features: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
        """
        claude code changed: new — Step 6 point 8. Treated strictly as a
        RESEARCH FEATURE for later IC/permutation testing (Phase 2B Step
        12), never as a trading signal — no entry/exit/threshold logic
        exists anywhere in this method or this module.

        Adds:
            price_change      — close-over-close return for this candle
                                 (causally safe: close[t] vs close[t-1],
                                 both already realized by candle t's close)
            cvd_change         — cvd[t] - cvd[t-1] (same causal safety)
            price_flow_divergence — sign(price_change) != sign(cvd_change),
                                 True where price and flow moved in
                                 OPPOSITE directions this candle. NaN
                                 (never coerced to False) wherever either
                                 input is NaN or exactly zero — a
                                 divergence claim needs both signs to
                                 exist, "no divergence" and "cannot say"
                                 are different things.
        """
        out = features.copy()
        price_change = close.reindex(features.index).diff()
        cvd_change = features["cvd"].diff()

        out["price_change"] = price_change
        out["cvd_change"] = cvd_change

        price_sign = np.sign(price_change)
        flow_sign = np.sign(cvd_change)
        both_nonzero = (price_sign != 0) & (flow_sign != 0) & price_sign.notna() & flow_sign.notna()

        divergence = pd.Series(np.nan, index=features.index, dtype="object")
        divergence.loc[both_nonzero] = price_sign.loc[both_nonzero] != flow_sign.loc[both_nonzero]
        out["price_flow_divergence"] = divergence

        return out


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """
    claude code changed: new — shared zero-denominator handling for
    buy_sell_ratio and avg_trade_size (Step 6 points 4/6's explicit
    "safe handling of zero denominators" / "proper missing/zero handling"
    requirement). 0/0 -> NaN (undefined, not 0 and not inf — no trades at
    all is a "we don't know" state, not "sell volume dominates").
    x/0 where x!=0 -> NaN, not inf — an unbounded ratio is not a useful
    research value and would silently poison any downstream mean/std
    calculation (inf propagates through arithmetic in ways NaN-aware
    pandas/numpy functions are built to handle explicitly).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = numerator / denominator
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    return ratio
