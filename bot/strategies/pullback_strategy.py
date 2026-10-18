# claude code changed: new file — PullbackStrategy; D-5 pre-registered 2026-10-18; H3/H4 intraday FX protocol

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PullbackStrategy:
    """
    Trend-pullback strategy for TRENDING_UP / TRENDING_DOWN markets.

    Logic (BUY in TRENDING_UP):
      1. EMA20 > EMA50  — uptrend confirmed at two timescales
      2. At least one of the last `pullback_lookback` bars closed at/below EMA20
         — the pullback actually reached the EMA level
      3. Current bar close > EMA20  — price reclaimed the EMA (trend resuming)
      4. Current close > previous close  — momentum direction matches trend
      5. RSI between rsi_min and rsi_max  — not in reversal territory
      Entry at next bar's open (completed-bar discipline in runner).
      SL = entry - ATR * atr_multiplier; TP = entry + SL_distance * rr_ratio.

    SELL in TRENDING_DOWN is the exact mirror.
    RANGING and HIGH_VOLATILITY: no signal.

    Pre-registered under D-5 (governance log 2026-10-18) for H3/H4.
    Parameters are locked; any change requires a new governance entry.
    """

    def __init__(
        self,
        fast_ema_period: int = 20,
        slow_ema_period: int = 50,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        rr_ratio: float = 2.0,
        pullback_lookback: int = 5,
        rsi_min: float = 35.0,
        rsi_max: float = 65.0,
        min_candles: int = 50,
    ):
        self.fast_ema_period  = fast_ema_period
        self.slow_ema_period  = slow_ema_period
        self.atr_period       = atr_period
        self.atr_multiplier   = atr_multiplier
        self.rr_ratio         = rr_ratio
        self.pullback_lookback = pullback_lookback
        self.rsi_min          = rsi_min
        self.rsi_max          = rsi_max
        self.min_candles      = min_candles
        self.name             = "PullbackStrategy"

        logger.info(
            f"[PullbackStrategy] Initialised | "
            f"EMA {fast_ema_period}/{slow_ema_period} | "
            f"ATR {atr_multiplier}x | R:R {rr_ratio} | "
            f"RSI [{rsi_min},{rsi_max}] | lookback {pullback_lookback}"
        )

    def evaluate(self, df: pd.DataFrame, regime_result) -> dict:
        """
        Evaluate current bar for a pullback entry.

        df:            OHLCV DataFrame (must have open/high/low/close)
        regime_result: RegimeResult from RegimeDetector

        Returns signal dict with keys: signal, entry, sl, tp, rsi, reason, strategy.
        Never returns None.
        """
        if len(df) < self.min_candles:
            return self._no_signal("not_enough_data")

        regime = regime_result.regime
        if regime not in ("TRENDING_UP", "TRENDING_DOWN"):
            return self._no_signal("non_trending_market")

        close = df["close"]

        # -- Indicators --
        ema_fast = close.ewm(span=self.fast_ema_period, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow_ema_period, adjust=False).mean()
        rsi      = self._rsi(close)
        atr      = self._atr(df)

        if atr is None or atr == 0:
            return self._no_signal("atr_unavailable")

        current_close    = float(close.iloc[-1])
        prev_close       = float(close.iloc[-2]) if len(df) >= 2 else current_close
        current_ema_fast = float(ema_fast.iloc[-1])
        current_ema_slow = float(ema_slow.iloc[-1])
        current_rsi      = float(rsi.iloc[-1])

        if pd.isna(current_rsi) or pd.isna(current_ema_fast) or pd.isna(current_ema_slow):
            return self._no_signal("indicator_not_ready")

        # RSI gate (same for both directions)
        if not (self.rsi_min <= current_rsi <= self.rsi_max):
            return self._no_signal("rsi_out_of_range")

        sl_distance = atr * self.atr_multiplier

        if regime == "TRENDING_UP":
            # Trend filter: EMA20 > EMA50
            if current_ema_fast <= current_ema_slow:
                return self._no_signal("ema_no_uptrend")

            # Pullback: at least one recent bar closed at/below EMA20
            lookback_closes = close.iloc[-self.pullback_lookback - 1 : -1]
            lookback_ema    = ema_fast.iloc[-self.pullback_lookback - 1 : -1]
            if not (lookback_closes <= lookback_ema).any():
                return self._no_signal("no_pullback")

            # Recovery: current bar reclaimed above EMA20
            if current_close <= current_ema_fast:
                return self._no_signal("no_recovery")

            # Momentum confirmation
            if current_close <= prev_close:
                return self._no_signal("no_momentum_confirmation")

            sl = round(current_close - sl_distance, 6)
            tp = round(current_close + sl_distance * self.rr_ratio, 6)

            logger.debug(
                f"[PullbackStrategy] BUY | RSI {current_rsi:.1f} | "
                f"EMA {current_ema_fast:.5f} | ATR {atr:.5f}"
            )
            return {
                "signal":    "BUY",
                "entry":     round(current_close, 6),
                "sl":        sl,
                "tp":        tp,
                "rsi":       current_rsi,
                "reason":    "pullback_buy",
                "strategy":  self.name,
                "structure": "TRENDING_UP",
            }

        else:  # TRENDING_DOWN
            # Trend filter: EMA20 < EMA50
            if current_ema_fast >= current_ema_slow:
                return self._no_signal("ema_no_downtrend")

            # Pullback: at least one recent bar closed at/above EMA20 (counter-trend bounce)
            lookback_closes = close.iloc[-self.pullback_lookback - 1 : -1]
            lookback_ema    = ema_fast.iloc[-self.pullback_lookback - 1 : -1]
            if not (lookback_closes >= lookback_ema).any():
                return self._no_signal("no_pullback")

            # Recovery: current bar reclaimed below EMA20
            if current_close >= current_ema_fast:
                return self._no_signal("no_recovery")

            # Momentum confirmation
            if current_close >= prev_close:
                return self._no_signal("no_momentum_confirmation")

            sl = round(current_close + sl_distance, 6)
            tp = round(current_close - sl_distance * self.rr_ratio, 6)

            logger.debug(
                f"[PullbackStrategy] SELL | RSI {current_rsi:.1f} | "
                f"EMA {current_ema_fast:.5f} | ATR {atr:.5f}"
            )
            return {
                "signal":    "SELL",
                "entry":     round(current_close, 6),
                "sl":        sl,
                "tp":        tp,
                "rsi":       current_rsi,
                "reason":    "pullback_sell",
                "strategy":  self.name,
                "structure": "TRENDING_DOWN",
            }

    def _rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs       = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame) -> float:
        try:
            high  = df["high"]
            low   = df["low"]
            close = df["close"]
            tr = np.maximum(
                high - low,
                np.maximum(
                    abs(high - close.shift(1)),
                    abs(low  - close.shift(1)),
                ),
            )
            atr = tr.rolling(self.atr_period).mean().iloc[-1]
            return float(atr) if not pd.isna(atr) else None
        except Exception as e:
            logger.warning(f"[PullbackStrategy] ATR calculation failed: {e}")
            return None

    def _no_signal(self, reason: str) -> dict:
        return {
            "signal":    "NO_SIGNAL",
            "entry":     0,
            "sl":        0,
            "tp":        0,
            "rsi":       0,
            "reason":    reason,
            "strategy":  self.name,
            "structure": "TRENDING",
        }
