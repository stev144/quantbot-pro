"""
grid_engine.py

Institutional-grade grid trading engine
for ranging market conditions.

Designed to work alongside:
- RegimeDetector
- SignalEngine
- Backtester

IMPORTANT:
Grid is NOT a signal generator.
It is a stateful execution engine.
"""

import pandas as pd

class GridEngine:

    def __init__(
        self,
        grid_levels: int = 5,
        risk_per_grid: float = 0.005,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
    ):
        self.grid_levels = grid_levels
        self.risk_per_grid = risk_per_grid
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

        # Grid state
        self.is_active = False
        self.grid_upper = None
        self.grid_lower = None
        self.grid_step = None

        self.buy_levels = []
        self.open_positions = []

        # Metrics
        self.total_fees = 0
        self.total_slippage = 0

    # =====================================================
    # GRID SETUP
    # =====================================================

    def calculate_grid_boundaries(self, df: pd.DataFrame):
        """
        Uses ATR to define grid boundaries.
        """

        df = df.copy()

        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = df["tr"].rolling(14).mean().iloc[-1]

        if pd.isna(atr) or atr <= 0:
            return None, None, None

        price = float(df["close"].iloc[-1])

        upper = price + atr * 1.5
        lower = price - atr * 1.5

        total_range = upper - lower
        step = total_range / (self.grid_levels * 2)

        # Avoid fee traps
        if step < price * 0.003:
            return None, None, None

        return upper, lower, step

    def setup_grid(self, price: float, balance: float):
        """
        Initializes grid levels.
        """

        self.buy_levels = []
        self.open_positions = []

        risk_amount = balance * self.risk_per_grid

        if not self.grid_step:
            return

        qty = risk_amount / self.grid_step

        for i in range(1, self.grid_levels + 1):
            buy_price = price - (self.grid_step * i)

            if buy_price < self.grid_lower:
                break

            self.buy_levels.append({
                "price": buy_price,
                "qty": qty,
                "filled": False,
                "level": i,
            })

        self.is_active = True

    # =====================================================
    # EXECUTION
    # =====================================================

    def process_candle(self, high, low, close):
        """
        Executes grid logic per candle.
        Returns completed trades.
        """

        if not self.is_active:
            return []

        executed_trades = []

        # ── BUY TRIGGERS ──
        for level in self.buy_levels:

            if level["filled"]:
                continue

            if low <= level["price"]:
                fill_price = level["price"] * (1 + self.slippage_rate)
                level["filled"] = True

                fee = fill_price * level["qty"] * self.fee_rate
                self.total_fees += fee

                self.open_positions.append({
                    "entry": fill_price,
                    "qty": level["qty"],
                    "target": level["price"] + self.grid_step,
                    "level_ref": level,
                })

        # ── SELL / EXIT ──
        for pos in list(self.open_positions):

            if high >= pos["target"]:

                exit_price = pos["target"] * (1 - self.slippage_rate)

                gross = (exit_price - pos["entry"]) * pos["qty"]

                fee = exit_price * pos["qty"] * self.fee_rate
                self.total_fees += fee

                slippage = abs(exit_price - pos["target"]) * pos["qty"]
                self.total_slippage += slippage

                net = gross - fee

                trade = {
                    "type": "GRID",
                    "direction": "LONG",
                    "entry_price": round(pos["entry"], 2),
                    "exit_price": round(exit_price, 2),
                    "quantity": round(pos["qty"], 8),
                    "profit": round(net, 4),
                    "exit_reason": "GRID_TP",
                }

                executed_trades.append(trade)

                # Reset level
                pos["level_ref"]["filled"] = False
                self.open_positions.remove(pos)

        return executed_trades

    # =====================================================
    # RISK CONTROL
    # =====================================================

    def circuit_breaker(self, price: float):
        """
        Stops grid if breakout occurs.
        """

        if not self.is_active:
            return False

        if price > self.grid_upper or price < self.grid_lower:
            self.is_active = False
            self._force_close(price)
            return True

        return False

    def _force_close(self, price: float):
        """
        Emergency exit.
        """

        for pos in self.open_positions:

            gross = (price - pos["entry"]) * pos["qty"]

            fee = price * pos["qty"] * self.fee_rate
            net = gross - fee

            self.total_fees += fee

        self.open_positions = []
        self.buy_levels = []

    # =====================================================
    # RESET (important for new regime cycles)
    # =====================================================

    def reset(self):
        self.is_active = False
        self.grid_upper = None
        self.grid_lower = None
        self.grid_step = None
        self.buy_levels = []
        self.open_positions = []