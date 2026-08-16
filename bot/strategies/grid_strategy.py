# bot/strategies/grid_strategy.py

from bot.engines.grid_engine import GridEngine
import numpy as np
import pandas as pd


class GridStrategy:

    def __init__(self, grid_engine):
        self.grid_engine = grid_engine

    def evaluate(self, df, regime_result):
        """
        Decision layer:
        decides whether grid should activate
        """

        if len(df) < 60:
            return None

        # =========================
        # ACTIVATION CONDITIONS
        # =========================

        if regime_result.regime != "ranging":
            return {
                "signal": "NO_GRID",
                "reason": "non_ranging_market"
            }

        if regime_result.confidence < 0.7:
            return {
                "signal": "NO_GRID",
                "reason": "low_regime_confidence"
            }

        # =========================
        # GRID PARAMETERS (dynamic)
        # =========================
        price = df["close"].iloc[-1]

        atr = self._atr(df)
        grid_step = atr * 0.8

        grid_upper = price + (atr * 1.5)
        grid_lower = price - (atr * 1.5)

        # =========================
        # ACTIVATE GRID
        # =========================
        self.grid_engine.setup_grid(
            current_price=price,
            grid_levels=5,
            grid_step=grid_step,
            grid_upper=grid_upper,
            grid_lower=grid_lower,
            risk_per_level=0.005
        )

        return {
            "signal": "GRID_ACTIVE",
            "reason": "ranging_confirmed"
        }

    def _atr(self, df):
        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift(1)),
                abs(df["low"] - df["close"].shift(1))
            )
        )
        return tr.rolling(14).mean().iloc[-1]