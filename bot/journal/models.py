# ============================================================
# journal/models.py
# TWO MODELS LIVE HERE — DO NOT CONFUSE THEM
#
# Model 1: Trade (YOUR EXISTING CLASS — UNCHANGED)
#   - Pure Python object
#   - Lives in memory during backtests
#   - No database involved
#   - Used by backtester engine
#
# Model 2: TradeRecord (NEW DJANGO DATABASE MODEL)
#   - Django ORM — writes to database
#   - Used by live bot for permanent storage
#   - Used by dashboard and analytics views
#
# Why keep both?
# Your backtester uses Trade objects internally.
# Your live bot needs to persist trades to the database.
# They serve different purposes — merging them would break both.
# ============================================================

from django.db import models    # Django ORM — only used by TradeRecord

# ============================================================
# MODEL 1: Trade — YOUR EXISTING CLASS (COMPLETELY UNCHANGED)
# This is your backtester trade object. Do not modify this.
# ============================================================

class Trade:
    """
    Represents a single trade in the backtest.
    Handles entry, exit, and performance metrics.
    """

    def __init__(
        self,
        direction,
        entry_price,
        sl,
        tp,
        quantity,
        trend,
        rsi,
        entry_index,
        risk_amount,
        reason=None
    ):

        # ===== ENTRY DATA =====
        self.direction = direction      # "LONG" or "SHORT"
        self.entry_price = entry_price  # Price at which trade was entered
        self.sl = sl                    # Stop loss price level
        self.tp = tp                    # Take profit price level
        self.quantity = quantity        # Number of units traded

        self.trend = trend              # Trend direction at entry time
        self.rsi = rsi                  # RSI value at entry time
        self.reason = reason            # Strategy reason for the signal

        self.entry_index = entry_index  # Candle index when trade opened
        self.risk_amount = risk_amount  # Dollar amount at risk on this trade

        # ===== EXIT DATA =====
        self.exit_price = None          # Filled when trade closes
        self.profit = None              # Calculated at close
        self.exit_reason = None         # "stop_loss", "take_profit", "force_close"
        self.exit_index = None          # Candle index when trade closed

        # ===== METRICS =====
        self.holding_candles = None     # How many candles the trade was held
        self.r_multiple = None          # Profit as a multiple of initial risk


    def close(self, exit_price, reason, exit_index):
        """Closes the trade and calculates all performance metrics."""

        # Record exit details
        self.exit_price = exit_price    # Actual exit price
        self.exit_reason = reason       # Why it closed
        self.exit_index = exit_index    # Which candle it closed on

        # Calculate holding time in candles
        self.holding_candles = exit_index - self.entry_index

        # Calculate raw profit based on direction
        if self.direction == "LONG":
            # Long profit: gained if price went up
            self.profit = (exit_price - self.entry_price) * self.quantity
        else:
            # Short profit: gained if price went down
            self.profit = (self.entry_price - exit_price) * self.quantity

        # Calculate R-multiple: how many times our risk did we make/lose
        if self.risk_amount and self.risk_amount != 0:
            self.r_multiple = self.profit / self.risk_amount
        else:
            # Avoid division by zero if risk_amount is missing
            self.r_multiple = 0


    def to_dict(self):
        """Safe UI + analytics output — converts trade to serialisable dict."""

        return {
            "direction": self.direction,

            # ENTRY
            "entry_price": round(self.entry_price, 6) if self.entry_price is not None else None,
            "sl":          round(self.sl, 6) if self.sl is not None else None,
            "tp":          round(self.tp, 6) if self.tp is not None else None,

            # EXIT
            "exit_price":  round(self.exit_price, 6) if self.exit_price is not None else None,
            "profit":      round(self.profit, 4) if self.profit is not None else 0,

            # METRICS
            "r_multiple":      round(self.r_multiple, 4) if self.r_multiple is not None else 0,
            "holding_candles": self.holding_candles if self.holding_candles is not None else 0,

            # IMPORTANT FOR ANALYTICS ENGINE
            "signal": self.direction,   # Unified with analytics expectations
            "rsi":    self.rsi,
            "reason": self.reason if self.reason else "no_reason",

            # META
            "exit_reason": self.exit_reason,
            "trend":       self.trend,
        }


# ============================================================
# MODEL 2: TradeRecord — NEW DJANGO DATABASE MODEL
#
# This is the persistent database version of a trade.
# Written by trade_logger.py when the live bot opens/closes trades.
# Read by journal_view.py and analytics to populate the dashboard.
#
# Naming: TradeRecord (not Trade) to avoid any name collision
# with the backtester Trade class above.
#
# After adding this model run:
#   python manage.py makemigrations
#   python manage.py migrate
# ============================================================

class TradeRecord(models.Model):
    """
    Permanent database record of a live bot trade.
    Created on entry, updated on exit by TradeLogger.
    """

    # ---- Identity ----

    # Trading pair e.g. "NEAR/USDT"
    symbol = models.CharField(max_length=20)

    # "BUY" or "SELL" — matches signal dict from strategy
    side = models.CharField(max_length=10)

    # Which strategy generated this signal
    strategy = models.CharField(max_length=50, default="MovingAverageStrategy")


    # ---- Entry Fields ----

    # Actual fill price from exchange (after slippage)
    entry_price = models.FloatField()

    # Stop loss price level — matches "sl" key in signal dict
    sl = models.FloatField()

    # Take profit price level — matches "tp" key in signal dict
    tp = models.FloatField()

    # Units actually filled by exchange
    quantity = models.FloatField()

    # Entry fee in USDT — normalised float from OrderManager
    fee_entry = models.FloatField(default=0.0)

    # Slippage on entry as percentage
    slippage_in = models.FloatField(default=0.0)

    # Exchange order ID — links record back to actual exchange order
    order_id = models.CharField(max_length=100)

    # RSI value at time of signal — for analytics pattern detection
    rsi_at_entry = models.FloatField(default=0.0)

    # Dollar amount risked on this trade — used for R-Multiple calculation
    risk_amount = models.FloatField(default=0.0)


    # ---- Exit Fields ----
    # Nullable — trade starts with no exit data

    # Actual exit fill price from exchange
    exit_price = models.FloatField(null=True, blank=True)

    # Exit fee in USDT
    fee_exit = models.FloatField(default=0.0)

    # Slippage on exit as a percentage
    slippage_out = models.FloatField(default=0.0)

    # Why the trade closed: "stop_loss", "take_profit", or "manual"
    exit_reason = models.CharField(max_length=20, null=True, blank=True)


    # ---- Performance Fields ----
    # All nullable — only calculated when trade closes

    # Gross P&L before fees: (exit - entry) * quantity
    gross_pnl = models.FloatField(null=True, blank=True)

    # Net P&L after both entry and exit fees — real account impact
    net_pnl = models.FloatField(null=True, blank=True)

    # R-Multiple: net_pnl / risk_amount
    # +1.8 means you made 1.8x your risk, -1.0 means full stop hit
    r_multiple = models.FloatField(null=True, blank=True)

    # How many candles the trade was held open
    holding_candles = models.IntegerField(null=True, blank=True)


    # ---- Status ----

    # "OPEN"  — trade is currently live on the exchange
    # "WIN"   — trade closed with positive net P&L
    # "LOSS"  — trade closed with negative net P&L
    status = models.CharField(max_length=10, default="OPEN")


    # ---- Timestamps ----

    # Set automatically when record is first created (entry time)
    created_at = models.DateTimeField(auto_now_add=True)

    # Updated automatically on every save — captures exit time on close
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        # Clean string for Django admin and log output
        return (
            f"{self.symbol} | {self.side} | {self.status} | "
            f"R: {self.r_multiple}R | P&L: ${self.net_pnl}"
        )

    class Meta:
        # Newest trades appear first in all queries and Django admin
        ordering = ["-created_at"]

        # Human readable labels in Django admin panel
        verbose_name = "Trade Record"
        verbose_name_plural = "Trade Records"