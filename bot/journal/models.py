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
    # claude code changed: 2026-10-07 decision journal Phase 1 (owner D6) — nullable link to the DecisionEvent
    # that produced this row. Additive; unused until live/dry-run wiring (a later, separately approved phase).
    decision_id = models.UUIDField(null=True, blank=True, db_index=True)


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


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# claude code changed: new — 2026-10-07 Trading Decision Journal, PHASE 1 (backtester-only recorder).
# Owner sign-off: TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md; vocabulary: bot/journal/taxonomy.py.
#   JournalRun     one backtest run (provenance header + compact coverage, D8); mutable ONLY while OPEN
#   DecisionEvent  one genuine evaluation point (OD-J13 backtester half), terminal status + gate vector; insert-only
#   MarketSnapshot point-in-time inputs of one decision (SIMULATED_CAUSAL in Phase 1); insert-only
#   JournalGap     an explicit journal defect (mapping failure / recorder failure); insert-only
# Insert-only follows the ResearchRecordIsImmutableError pattern (bot.research_lab.models): a correction is a
# NEW row pointing at the old one (DecisionEvent.supersedes), never an edit. QuerySet.update()/delete() and
# instance delete() are refused; bulk_create (pure INSERT) is how the recorder writes.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
class JournalRecordIsImmutableError(Exception):   # claude code changed: new
    """Raised when code tries to modify or delete a decision-journal record (insert-only, Rev B §13)."""


class _InsertOnlyQuerySet(models.QuerySet):   # claude code changed: new
    def update(self, **kwargs):
        raise JournalRecordIsImmutableError(f"{self.model.__name__} rows are insert-only (QuerySet.update refused)")

    def delete(self):
        raise JournalRecordIsImmutableError(f"{self.model.__name__} rows are insert-only (QuerySet.delete refused)")


class _InsertOnlyModel(models.Model):   # claude code changed: new
    objects = _InsertOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise JournalRecordIsImmutableError(f"{type(self).__name__} {self.pk} is insert-only; write a superseding row")
        # an explicit primary key would otherwise make Django try an UPDATE first and silently overwrite an
        # existing row; force_insert turns any duplicate into an IntegrityError (found by the duplicate test)
        kwargs["force_insert"] = True
        kwargs.pop("force_update", None)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise JournalRecordIsImmutableError(f"{type(self).__name__} rows are insert-only (delete refused)")


JOURNAL_RUN_OPEN, JOURNAL_RUN_CLOSED, JOURNAL_RUN_INCOMPLETE = "OPEN", "CLOSED", "INCOMPLETE"   # claude code changed: new
_RUN_MUTABLE_WHILE_OPEN = ("status", "closed_at", "coverage", "incomplete_reasons", "n_decisions")   # claude code changed: new


class JournalRun(models.Model):   # claude code changed: new
    """One recorded backtest run. Created OPEN at run start; closed exactly once (CLOSED or INCOMPLETE); after
    that it is immutable. Provenance reuses bot.quant_core.provenance (code_state, config_hash, data_version)."""
    run_id = models.CharField(max_length=80, unique=True)
    environment = models.CharField(max_length=10)                    # taxonomy.ENVIRONMENTS (OD-J15); Phase 1: BACKTEST
    execution_mode = models.CharField(max_length=10)                 # EXECUTION_MODES above; Phase 1: RESEARCH (owner 2026-10-07)
    symbol = models.CharField(max_length=40)
    asset_class = models.CharField(max_length=20)
    venue = models.CharField(max_length=20)
    timeframe = models.CharField(max_length=10)
    status = models.CharField(max_length=12, default=JOURNAL_RUN_OPEN)
    started_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    # provenance (bot.quant_core.provenance; never re-implemented here)
    code_commit = models.CharField(max_length=64, null=True, blank=True)
    code_dirty = models.BooleanField(null=True)
    dirty_digest = models.CharField(max_length=64, null=True, blank=True)
    methodology_version = models.CharField(max_length=60)            # NON-CANONICAL-RESEARCH-ENGINE for the backtester
    protocol_hash = models.CharField(max_length=64, blank=True, default="")   # "" = not under a canonical protocol
    config_hash = models.CharField(max_length=64)
    config = models.JSONField(default=dict)
    data_version = models.CharField(max_length=64)
    data_version_detail = models.JSONField(default=dict)
    zones_version = models.CharField(max_length=40)
    zones_hash = models.CharField(max_length=64)
    market_profile_hash = models.CharField(max_length=64, blank=True, default="")
    strategy_set = models.CharField(max_length=120)
    strategy_version = models.CharField(max_length=80)
    taxonomy_version = models.CharField(max_length=40)
    schema_version = models.CharField(max_length=40)
    gate_order_version = models.CharField(max_length=40)
    feature_set_version = models.CharField(max_length=40)
    coverage_version = models.CharField(max_length=40)
    evidence_status = models.CharField(max_length=80)
    coverage = models.JSONField(default=dict)
    incomplete_reasons = models.JSONField(default=list)
    n_decisions = models.IntegerField(default=0)

    objects = _InsertOnlyQuerySet.as_manager()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(status__in=[JOURNAL_RUN_OPEN, JOURNAL_RUN_CLOSED,
                                                                              JOURNAL_RUN_INCOMPLETE]),
                                              name="journalrun_status_valid")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if old.status != JOURNAL_RUN_OPEN:
                raise JournalRecordIsImmutableError(f"JournalRun {self.run_id} is {old.status}; closed runs are immutable")
            for f in self._meta.concrete_fields:
                if f.name not in _RUN_MUTABLE_WHILE_OPEN and f.attname != "id" and \
                        getattr(old, f.attname) != getattr(self, f.attname):
                    raise JournalRecordIsImmutableError(f"JournalRun.{f.name} is fixed at run start")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise JournalRecordIsImmutableError("JournalRun rows are never deleted")

    def __str__(self):
        return f"JournalRun {self.run_id} {self.environment} {self.symbol} {self.status}"


class DecisionEvent(_InsertOnlyModel):   # claude code changed: new
    """One genuine evaluation point and its terminal disposition. Holds ONLY decision-time information plus the
    terminal status (and, for EXECUTED, the link to the simulated trade — whose fill facts stay in the backtest
    result, D6). Never a post-decision outcome (those belong to the future DecisionLabel, Phase 2)."""
    decision_id = models.UUIDField(primary_key=True)                 # deterministic uuid5(run_id, symbol, candle)
    run = models.ForeignKey(JournalRun, on_delete=models.PROTECT, related_name="decisions")
    environment = models.CharField(max_length=10)                    # copies of the run identity (validated equal)
    execution_mode = models.CharField(max_length=10)
    symbol = models.CharField(max_length=40)
    asset_class = models.CharField(max_length=20)
    venue = models.CharField(max_length=20)
    timeframe = models.CharField(max_length=10)
    candle_index = models.IntegerField()
    candle_open_time = models.DateTimeField()
    information_time = models.DateTimeField()                        # candle close = open + timeframe
    research_zone = models.CharField(max_length=40)
    zone_basis = models.CharField(max_length=20, default="CANDLE_OPEN_TIME")
    zones_hash = models.CharField(max_length=64)
    strategy = models.CharField(max_length=60)
    strategy_version = models.CharField(max_length=80)
    direction = models.CharField(max_length=6, blank=True, default="")   # LONG / SHORT when a candidate existed
    status = models.CharField(max_length=24)
    reason_code = models.CharField(max_length=40, null=True, blank=True)
    reason_stage = models.CharField(max_length=20, null=True, blank=True)
    origin = models.CharField(max_length=20, null=True, blank=True)
    raw_reason = models.CharField(max_length=200, blank=True, default="")
    reason_detail = models.JSONField(default=dict)
    lifecycle_stages = models.JSONField(default=list)
    gate_vector = models.JSONField(default=dict)
    gate_order_version = models.CharField(max_length=40)
    planned_entry = models.FloatField(null=True, blank=True)
    planned_stop = models.FloatField(null=True, blank=True)
    planned_target = models.FloatField(null=True, blank=True)
    planned_risk_per_unit = models.FloatField(null=True, blank=True)
    planned_risk_amount = models.FloatField(null=True, blank=True)   # null in the backtester: sizing happens at the fill
    planned_reward_risk = models.FloatField(null=True, blank=True)
    expected_r = models.FloatField(null=True, blank=True)            # null: no validated expectancy model exists
    estimated_cost = models.JSONField(null=True, blank=True)
    risk_state = models.JSONField(default=dict)
    portfolio_state = models.JSONField(default=dict)
    liquidity_state = models.JSONField(default=dict)
    financing_state = models.JSONField(default=dict)
    data_quality_state = models.JSONField(default=dict)
    execution_eligible = models.BooleanField(default=False)
    backtest_trade_id = models.CharField(max_length=100, null=True, blank=True)   # "<run_id>:T000001" (D6)
    backtest_trade_seq = models.IntegerField(null=True, blank=True)
    fill_candle_index = models.IntegerField(null=True, blank=True)
    methodology_version = models.CharField(max_length=60)
    protocol_hash = models.CharField(max_length=64, blank=True, default="")
    config_hash = models.CharField(max_length=64)
    data_version = models.CharField(max_length=64)
    taxonomy_version = models.CharField(max_length=40)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "symbol", "candle_open_time"], name="one_decision_per_candle_per_run"),
            models.UniqueConstraint(fields=["run", "backtest_trade_seq"], name="one_decision_per_backtest_trade"),
            models.CheckConstraint(condition=models.Q(information_time__gt=models.F("candle_open_time")),
                                   name="decision_information_after_open"),
            models.CheckConstraint(condition=models.Q(status__in=["EXECUTED", "NO_SIGNAL", "REJECTED", "REGIME_REJECTED",
                                                                   "COST_REJECTED", "LIQUIDITY_REJECTED", "TIMING_REJECTED",
                                                                   "RISK_REJECTED", "PORTFOLIO_REJECTED", "BORROW_REJECTED",
                                                                   "DATA_REJECTED", "DUPLICATE_REJECTED", "EXECUTION_FAILED",
                                                                   "SYSTEM_REJECTED", "CENSORED_END_OF_DATA"]),
                                   name="decision_status_terminal"),
            models.CheckConstraint(condition=models.Q(status="EXECUTED", backtest_trade_seq__isnull=False) |
                                   (~models.Q(status="EXECUTED") & models.Q(backtest_trade_seq__isnull=True)),
                                   name="decision_trade_link_iff_executed"),
        ]


SNAPSHOT_POINT_IN_TIME_LIVE, SNAPSHOT_SIMULATED_CAUSAL = "POINT_IN_TIME_LIVE", "SIMULATED_CAUSAL"   # claude code changed: new


class MarketSnapshot(_InsertOnlyModel):   # claude code changed: new
    """What the engine could see at the decision. RECONSTRUCTED_POST_HOC snapshots can never be stored here
    (DB check) — a reconstruction must never masquerade as a point-in-time snapshot (Rev B §8)."""
    decision = models.OneToOneField(DecisionEvent, on_delete=models.PROTECT, primary_key=True, related_name="snapshot")
    snapshot_kind = models.CharField(max_length=24)
    feature_set_version = models.CharField(max_length=40)
    information_time = models.DateTimeField()
    window_start_time = models.DateTimeField()
    window_end_time = models.DateTimeField()                         # open time of the last candle the engine saw
    window_rows = models.IntegerField()
    input_window_sha256 = models.CharField(max_length=64)
    values = models.JSONField(default=dict)
    available_at = models.JSONField(default=dict)                    # feature -> ISO time; every one <= information_time
    missing = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(snapshot_kind__in=[SNAPSHOT_POINT_IN_TIME_LIVE, SNAPSHOT_SIMULATED_CAUSAL]),
                                   name="snapshot_kind_point_in_time_only"),
            models.CheckConstraint(condition=models.Q(window_end_time__lt=models.F("information_time")),
                                   name="snapshot_window_before_information_time"),
        ]


GAP_MAPPING_FAILURE, GAP_RECORDER_FAILURE = "MAPPING_FAILURE", "RECORDER_FAILURE"   # claude code changed: new


class JournalGap(_InsertOnlyModel):   # claude code changed: new
    """An explicit journal defect. Any gap makes its run INCOMPLETE (never complete research evidence)."""
    run = models.ForeignKey(JournalRun, on_delete=models.PROTECT, related_name="gaps")
    kind = models.CharField(max_length=20)
    candle_index = models.IntegerField(null=True, blank=True)
    candle_open_time = models.DateTimeField(null=True, blank=True)
    raw_reason = models.CharField(max_length=200, blank=True, default="")
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=[GAP_MAPPING_FAILURE, GAP_RECORDER_FAILURE]),
                                              name="journalgap_kind_valid")]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# claude code changed: new — 2026-10-07 Decision Journal PHASE 2 (owner-delegated OD-J2/J3/J4/J9/J17).
#   DecisionLabel     post-decision OUTCOME of a decision under a frozen hypothetical rule or a fixed horizon —
#                     always NOT_EXECUTED, never linked to TradeRecord (real P&L), never read by real-P&L code.
#   PostTradeMetrics  derived post-trade metrics of an EXECUTED simulated trade (bar-resolution MAE/MFE, fill
#                     shortfall); recomputable, never edits the trade itself.
# Both insert-only. Label values for decisions outside zone S are read-guarded in the UI (OD-J16).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
LABEL_STATUSES = ("OK", "CENSORED", "UNTESTABLE", "SEALED", "COST_UNAVAILABLE")   # claude code changed: new


class DecisionLabel(_InsertOnlyModel):   # claude code changed: new
    decision = models.ForeignKey(DecisionEvent, on_delete=models.PROTECT, related_name="labels")
    run = models.ForeignKey(JournalRun, on_delete=models.PROTECT, related_name="labels")
    outcome_kind = models.CharField(max_length=32)                    # FORWARD_RETURN_NOT_EXECUTED | COUNTERFACTUAL_NOT_EXECUTED
    label_type = models.CharField(max_length=24)                      # FORWARD_RETURN | COUNTERFACTUAL_RULE
    label_version = models.CharField(max_length=40)
    rule_id = models.CharField(max_length=64, blank=True, default="")
    variant = models.CharField(max_length=24)                         # NONE | BASELINE_STOP_FIRST | BOUND_TARGET_FIRST
    horizon_candles = models.IntegerField()                           # h for forward returns; H (max hold) for the rule
    information_time = models.DateTimeField()                         # copy of the decision's, for the causality check
    label_window_start = models.DateTimeField()                       # open of candle t+1 (= information_time)
    label_window_end = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20)
    status_reason = models.CharField(max_length=200, blank=True, default="")
    research_zone = models.CharField(max_length=40)                   # zone of the DECISION (read guard key)
    window_end_zone = models.CharField(max_length=40, blank=True, default="")
    gross_return = models.FloatField(null=True, blank=True)           # forward return of the asset (direction-free)
    fill_time = models.DateTimeField(null=True, blank=True)
    fill_price = models.FloatField(null=True, blank=True)
    exit_time = models.DateTimeField(null=True, blank=True)
    exit_price = models.FloatField(null=True, blank=True)
    exit_reason = models.CharField(max_length=20, blank=True, default="")
    gross_r = models.FloatField(null=True, blank=True)
    net_r = models.FloatField(null=True, blank=True)
    cost_detail = models.JSONField(default=dict)
    mae_bar_1h_r = models.FloatField(null=True, blank=True)
    mfe_bar_1h_r = models.FloatField(null=True, blank=True)
    mae_excl_exit_bar_r = models.FloatField(null=True, blank=True)
    mfe_excl_exit_bar_r = models.FloatField(null=True, blank=True)
    bars_to_exit = models.IntegerField(null=True, blank=True)
    path_resolution = models.CharField(max_length=12, default="BAR_1H")
    ambiguity = models.CharField(max_length=32, default="NONE")
    outcome_assumption = models.CharField(max_length=24, default="RESOLVED")
    data_version = models.CharField(max_length=64)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["decision", "label_type", "label_version", "horizon_candles", "variant"],
                                    name="one_label_per_decision_type_version_horizon_variant"),
            models.CheckConstraint(condition=models.Q(label_window_start__gte=models.F("information_time")),
                                   name="label_window_not_before_decision"),
            models.CheckConstraint(condition=models.Q(status__in=list(LABEL_STATUSES)), name="label_status_valid"),
            models.CheckConstraint(condition=~models.Q(status="SEALED") | (models.Q(gross_return__isnull=True) &
                                   models.Q(gross_r__isnull=True) & models.Q(net_r__isnull=True)),
                                   name="sealed_label_has_no_values"),
            models.CheckConstraint(condition=models.Q(outcome_kind__endswith="_NOT_EXECUTED"), name="label_is_not_executed"),
        ]


class PostTradeMetrics(_InsertOnlyModel):   # claude code changed: new
    decision = models.OneToOneField(DecisionEvent, on_delete=models.PROTECT, primary_key=True, related_name="post_trade")
    run = models.ForeignKey(JournalRun, on_delete=models.PROTECT, related_name="post_trade_metrics")
    metrics_version = models.CharField(max_length=40)
    trade_ref = models.CharField(max_length=100)                      # backtest_trade_id (or TradeRecord pk later)
    status = models.CharField(max_length=20, default="OK")
    status_reason = models.CharField(max_length=200, blank=True, default="")
    realized_risk_per_unit = models.FloatField(null=True, blank=True)  # |fill - stop|, the backtester's own R basis
    mae_bar_1h_r = models.FloatField(null=True, blank=True)
    mfe_bar_1h_r = models.FloatField(null=True, blank=True)
    mae_excl_exit_bar_r = models.FloatField(null=True, blank=True)
    mfe_excl_exit_bar_r = models.FloatField(null=True, blank=True)
    bars_held = models.IntegerField(null=True, blank=True)
    bars_to_mae = models.IntegerField(null=True, blank=True)
    bars_to_mfe = models.IntegerField(null=True, blank=True)
    implementation_shortfall_bps = models.FloatField(null=True, blank=True)   # fill vs decision close, adverse > 0
    fill_vs_rule_reference_bps = models.FloatField(null=True, blank=True)     # fill vs open of t+1, adverse > 0
    excursion_resolution = models.CharField(max_length=12, default="BAR_1H")
    computed_at = models.DateTimeField(auto_now_add=True)

