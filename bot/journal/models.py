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

            # claude code changed: new — purely additive keys (Type B OOS evaluator mission).
            # Positions within whatever DataFrame slice the caller ran, e.g.
            # for indexing back into a Backtester run's own df.index to
            # recover real entry/exit timestamps. Nothing previously read
            # this dict by an exact/exhaustive key set (checked before adding),
            # so this cannot change any existing caller's behavior.
            "entry_index": self.entry_index,
            "exit_index":  self.exit_index,

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

# claude code changed: Post-audit Phase 1 journal-mode separation — the only
# values TradeRecord/ExecutionIncident.execution_mode may hold.
MODE_LIVE, MODE_DRY_RUN, MODE_RESEARCH, MODE_UNKNOWN = "LIVE", "DRY_RUN", "RESEARCH", "UNKNOWN"
EXECUTION_MODES = (MODE_LIVE, MODE_DRY_RUN, MODE_RESEARCH, MODE_UNKNOWN)


def execution_mode_for(dry_run: bool) -> str:   # claude code changed: new — writers derive the mode from their engine's flag, never from data
    return MODE_DRY_RUN if dry_run else MODE_LIVE


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


    # ---- Provenance Fields ----
    # claude code changed: new — Phase 4 (DB persistence of trade
    # provenance). Mirrors bot/engines/trade_narrative.py's TradeNarrative
    # fields of the same name — see bot/research/validated_feature_registry.py
    # for what research_verdict/production_eligible actually mean. All
    # defaulted so this is a purely additive migration.

    # SUPPORTED / CONDITIONAL / WEAK / REJECTED / UNTESTED at the time this
    # trade was placed — the strategy's verdict can change over time as
    # research evolves, so this is a point-in-time snapshot, not a live link.
    research_verdict = models.CharField(max_length=20, default="", blank=True)

    # Whether the strategy that produced this trade had a SUPPORTED verdict
    # at entry time — should always be True for any trade that reaches this
    # table once StrategyRouter's production-validation gate is in place,
    # but recorded explicitly rather than assumed.
    production_eligible = models.BooleanField(default=False)

    # The evidence cited for research_verdict above, e.g. which validator/
    # permutation-test results justified or blocked this strategy.
    verdict_rejection_reason = models.TextField(default="", blank=True)

    # Full human-readable narrative text — see TradeNarrativeGenerator.
    entry_narrative = models.TextField(default="", blank=True)
    exit_narrative = models.TextField(default="", blank=True)


    # ---- Venue ----
    # claude code changed: new — Kraken Multi-Venue Execution, Step 15.
    # Which exchange this trade actually executed on — matches
    # ExchangeAdapter.venue_id / ExecutionEngine.venue_id (Steps 2, 10).
    # Defaults to "binance" so every pre-existing row and every caller
    # that doesn't pass venue= is unaffected — this is a purely additive
    # migration, same discipline as the Provenance Fields block above.
    venue = models.CharField(max_length=20, default="binance", blank=True)


    # ---- Pair-trade linking ----
    # claude code changed: new — live pairs-trading pilot. A stat-arb
    # pair trade always opens TWO TradeRecord rows (one per leg/symbol) —
    # there is no schema for a single row to represent both legs. These
    # two nullable fields link them: both legs of one pair trade share
    # the same pair_trade_id, and leg distinguishes which side of the
    # spread each row represents. Nullable/blank so every pre-existing
    # single-symbol row (the regime bot's trades) is unaffected — this
    # is a purely additive migration, same discipline as the Provenance
    # and Venue blocks above. The existing unique-OPEN constraint below
    # is scoped to (symbol, venue), not pair_trade_id, so two legs (two
    # different symbols) being simultaneously OPEN was already
    # unblocked before this field existed — no constraint change needed.
    pair_trade_id = models.UUIDField(null=True, blank=True, db_index=True)
    leg = models.CharField(
        max_length=1,
        null=True,
        blank=True,
        choices=[("A", "Leg A"), ("B", "Leg B")],
    )


    # ---- DCA-cycle linking ----
    # claude code changed: new — DCA/Martingale research engine (Phase 1
    # slice, bot/research/dca_engine.py). A DCA cycle can open up to 7
    # TradeRecord rows (1 initial + up to 6 DCA levels) before it closes —
    # there is no schema for one row to represent a multi-fill averaged
    # position, same structural problem the pair-trade fields above
    # already solved for a different shape (2 simultaneous legs instead
    # of up to 7 sequential fills). Mirrors that exact precedent: all
    # rows sharing one cycle share the same cycle_id, and dca_level
    # distinguishes which fill each row represents (0 = initial leg, 1-6
    # = DCA level N). Nullable/blank so every pre-existing row (regime
    # bot, pairs pilot) is unaffected — purely additive, same discipline
    # as every other block in this model. Not yet written by any live
    # code path — this migration exists so the schema is ready before
    # paper/live execution is built (Phase 18-19), which is explicitly
    # OUT of scope for this first research-only slice.
    cycle_id = models.UUIDField(null=True, blank=True, db_index=True)
    dca_level = models.IntegerField(null=True, blank=True)

    # claude code changed: Stage 2D Rev B D-1 — durable C5 provenance. True when
    # this leg was CLOSED while the live engine's reconciliation flag was
    # active (a live polling gap not yet reconciled). Exits stay allowed for
    # risk management, but such a trade is never clean parity/research
    # evidence. False = a clean exit (and every non-pairs row).
    reconciliation_affected = models.BooleanField(default=False)
    reconciliation_gaps = models.TextField(default="", blank=True)   # claude code changed: D-1 — ";"-joined ISO open times live did not receive
    # claude code changed: Post-audit Phase 1 A4 — explicit cost/fill labels.
    # fill_model: how the entry (and, after "|exit:", the exit) price was
    # obtained — "EXCHANGE" for a real fill, "DRY_RUN_TOUCH+SLIPPAGE" or
    # "DRY_RUN_LAST_FALLBACK+SLIPPAGE" for a simulated one (full fill assumed).
    # borrow_cost_status: KNOWN (exchange-reported interest), UNAVAILABLE
    # (dry run: interest not modelled — never presented as a real zero), or
    # NOT_APPLICABLE (long leg). "" on rows written before this field existed.
    fill_model = models.CharField(max_length=80, default="", blank=True)
    borrow_cost_status = models.CharField(max_length=20, default="", blank=True)
    # claude code changed: Post-audit Phase 1 — journal-mode separation. Every row
    # says HOW it was produced, so simulated P&L can never be read as account P&L
    # (or feed the live drawdown guard): LIVE (a real exchange order), DRY_RUN
    # (in-process fill simulation against live prices), RESEARCH (offline
    # backtest/research output), UNKNOWN (cannot be established — consumers that
    # need a definite mode fail closed on it). Set explicitly by the writer from
    # its engine's dry_run flag, never inferred at read time. Migration 0014
    # backfills existing rows from their order id (dryrun-/DRYRUN- → DRY_RUN,
    # anything else → UNKNOWN; a row is never ASSUMED live).
    execution_mode = models.CharField(max_length=10, default="UNKNOWN", db_index=True)
    # Provenance of the decision that produced the row (same values SignalRejection
    # records); "" when the writer had none (e.g. the main single-symbol bot).
    methodology_version = models.CharField(max_length=40, default="", blank=True)
    protocol_hash = models.CharField(max_length=64, default="", blank=True)
    data_version = models.CharField(max_length=64, default="", blank=True)
    # When the exit was journaled. updated_at is auto_now (moves on ANY later
    # save), so it cannot order the realised-P&L path; null on rows closed
    # before this field existed (readers fall back to updated_at, labelled).
    closed_at = models.DateTimeField(null=True, blank=True)


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

        # claude code changed: new — fixes a real bug found during Issue 2
        # testing: bot/journal/trade_logger.py's log_exit() does
        # TradeRecord.objects.get(symbol=symbol, status="OPEN"), which
        # raises MultipleObjectsReturned if more than one OPEN row exists
        # for the same symbol. Nothing previously prevented that at the DB
        # level — only an in-memory check in ExecutionEngine.execute_signal()
        # (PositionTracker.has_position()), which resets to empty on every
        # process restart. A crash/restart with a real position still open
        # could let a fresh signal open a second position, writing a second
        # OPEN TradeRecord for the same symbol. This partial unique index
        # makes that structurally impossible at the database level — the
        # INSERT itself fails — rather than relying solely on Python-level
        # checks that a crash can bypass.
        #
        # claude code changed: was fields=["symbol"] — Kraken Multi-Venue
        # Execution, Step 15. symbol-only scoping meant ExecutionCoordinator
        # (Step 10) opening the SAME symbol on two DIFFERENT venues at once
        # — a real, intended capability — would hit this constraint on the
        # second INSERT, silently losing that trade's database record
        # (caught by TradeLogger.log_entry()'s broad except Exception).
        # Scoping by (symbol, venue) together still blocks the original bug
        # (a true duplicate: same symbol AND same venue, both OPEN) while
        # correctly permitting the same symbol OPEN on two different
        # venues. No behavior change for the default single-venue path —
        # every existing row has venue="binance".
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "venue"],
                condition=models.Q(status="OPEN"),
                name="unique_open_trade_per_symbol",
            )
        ]


class SignalRejection(models.Model):
    """claude code changed: Stage 2D Rev B D-2 — durable record of a canonical
    entry REJECTION (C1 ENTRY_SIGN_MISMATCH): the intended direction disagreed
    with the held-spread sign at the decision candle, so no order, no trade and
    no P&L exist for it. One row per rejected decision; survives restarts. Part
    of the trade journal, next to TradeRecord — not a separate logging system."""
    pair_name = models.CharField(max_length=60, db_index=True)
    venue = models.CharField(max_length=20, default="binance")
    decision_timestamp = models.DateTimeField(db_index=True)   # cutoff t (candle open time)
    intended_direction = models.CharField(max_length=20)       # LONG_SPREAD | SHORT_SPREAD
    zscore = models.FloatField()                                # the entry z that generated the signal
    held_spread = models.FloatField()                           # h_t = ln P_A,t − β_e ln P_B,t − α_e
    alpha_e = models.FloatField()
    beta_e = models.FloatField()
    price_a = models.FloatField()                               # decision-candle closes used for h_t
    price_b = models.FloatField()
    reason = models.CharField(max_length=40)                    # ENTRY_SIGN_MISMATCH
    methodology_version = models.CharField(max_length=40)
    protocol_hash = models.CharField(max_length=64)
    data_version = models.CharField(max_length=64, default="", blank=True)   # the seed record's content data version, if the engine was seeded canonically
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decision_timestamp"]
        constraints = [models.UniqueConstraint(fields=["pair_name", "venue", "decision_timestamp", "reason"],
                                               name="unique_signal_rejection_per_decision")]

    def __str__(self):
        return f"{self.pair_name} {self.decision_timestamp} {self.intended_direction} {self.reason}"



class ExecutionIncident(models.Model):
    """claude code changed: Post-audit Phase 1 A1/A3 — durable record of a
    pairs execution failure (a leg failed mid-open, a close failed, or a close
    was blocked by exchange rules). requires_manual_review=True keeps the pair
    disabled across restarts (pairs_bot_runner.reconcile_pairs) until a human
    resolves the incident; the system never retries such a pair by itself."""
    KIND_CHOICES = ("OPEN_LEG_FAILURE", "CLOSE_LEG_FAILURE", "CLOSE_BLOCKED_EXCHANGE_RULES")
    STATE_CHOICES = ("NO_EXPOSURE", "UNWOUND_CONFIRMED", "MANUAL_REVIEW_REQUIRED")
    pair_name = models.CharField(max_length=60, db_index=True)
    pair_trade_id = models.UUIDField(null=True, blank=True, db_index=True)
    kind = models.CharField(max_length=40)
    state = models.CharField(max_length=40)
    requires_manual_review = models.BooleanField(default=True)
    legs_filled = models.TextField(default="", blank=True)        # e.g. "A:BUY 10.0 @ 1.0"
    legs_open_after = models.TextField(default="", blank=True)    # legs the system believes are still open
    error = models.TextField(default="", blank=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(default="", blank=True)
    execution_mode = models.CharField(max_length=10, default="UNKNOWN", db_index=True)   # claude code changed: Post-audit Phase 1 journal-mode separation — LIVE / DRY_RUN / UNKNOWN, same vocabulary as TradeRecord
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.pair_name} {self.kind} {self.state} resolved={self.resolved}"
