# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/entry_exit_engine.py
#
# ENTRY AND EXIT RULES ENGINE
# AVAX/ATOM Kalman Pairs Trading Strategy
# QuantiBot Pro Research Pipeline — Missing Piece 4 of 6
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# WHERE THIS MODULE SITS IN THE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
# FEEDS FROM (reads output of these modules):
#   kalman_filter_engine.py  → research_data/AVAX_USDT_ATOM_USDT_kalman.csv
#       Provides: kalman_zscore, kalman_zscore_lag1, pair_signal_dynamic,
#                 pair_signal_momentum, pair_signal_uw, kalman_beta,
#                 kalman_spread, prediction_error, beta_uncertainty
#
# FEEDS INTO (its output is consumed by):
#   [Missing Piece 5] walk_forward_engine.py  → validates on unseen data
#   [Missing Piece 6] live_monitor.py         → real-time signal monitoring
#   [Future]          execution_engine.py     → generates actual Binance orders
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS MODULE DOES
# ─────────────────────────────────────────────────────────────────────────────
#
# The Kalman filter engine proved:
#   IC = 0.5245  (STRONG KEEP — confirmed by institutional validator)
#   Win rate = 68-71% across ALL years 2020-2025
#   Regime-agnostic: works in bull (2021), bear (2022), institutional (2025)
#   Signal frequency: ~2,470 entry opportunities over 5 years = ~10 per week
#
# Now we need rules that translate the z-score signal into actual trades.
# Specifically:
#   WHEN exactly to enter (entry threshold + confirmation filter)
#   HOW MUCH to trade (Kelly-based position sizing)
#   WHEN to exit for profit (z-score target)
#   WHEN to exit for loss (stop loss threshold)
#   WHEN to exit by time (time stop — 2 × half-life)
#
# ─────────────────────────────────────────────────────────────────────────────
# THE AVAX/ATOM PAIRS TRADE — HOW IT WORKS
# ─────────────────────────────────────────────────────────────────────────────
#
# The Kalman filter continuously estimates the equilibrium relationship:
#     log(AVAX_price) = α_t + β_t × log(ATOM_price)
#
# The spread is: spread_t = log(AVAX) - α_t - β_t × log(ATOM)
# The z-score is: (spread_t - rolling_mean) / rolling_std
#
# When z-score > +ENTRY_THRESHOLD:
#     AVAX is EXPENSIVE relative to ATOM → AVAX will fall OR ATOM will rise
#     Trade: SHORT AVAX + LONG ATOM (in the β_t ratio)
#     This is a MARKET NEUTRAL position — we do not care if crypto goes up or down
#     We only care that AVAX and ATOM converge back toward equilibrium
#
# When z-score < -ENTRY_THRESHOLD:
#     AVAX is CHEAP relative to ATOM → AVAX will rise OR ATOM will fall
#     Trade: LONG AVAX + SHORT ATOM (in the β_t ratio)
#
# Exit when z-score returns to near zero (equilibrium restored)
# Stop out if z-score moves further against us (relationship breaking down)
# Time stop if position held beyond 2 × half-life (119.9h × 2 = 240h)
#
# ─────────────────────────────────────────────────────────────────────────────
# PARAMETERS — DERIVED FROM OUR RESEARCH, NOT GUESSED
# ─────────────────────────────────────────────────────────────────────────────
#
# ENTRY_THRESHOLD = 2.0
#   Derived from: 5.7% of candles have |z| > 2.0 = ~10 signals per week
#   At IC = 0.5245, z > 2.0 signals have sufficient edge to cover costs
#   Lower threshold (1.5) gives more signals but lower win rate per trade
#   Higher threshold (3.0) gives fewer but higher-confidence signals
#
# CONFIRMATION_REQUIRED = True
#   kalman_zscore_lag1 must agree with current z-score direction
#   Derived from: IC of kalman_zscore_lag1 = 0.3905 (STRONG KEEP)
#   This filter eliminates single-candle spikes from manipulation/noise
#
# EXIT_ZSCORE_TARGET = 0.3
#   Derived from: spread mean-reverts to zero by definition (Kalman property)
#   Taking profit at 0.3 (near zero) captures most of the reversion move
#   Holding to exactly zero often means giving back some profit
#
# STOP_LOSS_ZSCORE = 4.5
#   Derived from: Kalman z-score was clipped at ±5.0 during engine run
#   z > 4.5 means the spread has moved 4.5 standard deviations against us
#   In 5 years of data this almost always precedes a cointegration break
#   or a major structural event — the correct action is to exit immediately
#
# TIME_STOP_HOURS = 240
#   Derived from: 2 × half-life of 119.9 hours = 239.8 hours ≈ 240 hours
#   If the spread has not reverted in twice the expected time, something
#   structural has changed. Do not hold hoping for an eventual reversion.
#
# KELLY_SAFETY_FRACTION = 0.25
#   Full Kelly sizing is mathematically optimal but practically dangerous
#   because IC estimates have uncertainty. 25% of Kelly (quarter-Kelly)
#   is the institutional standard for live deployment of a new strategy.
#   As the strategy accumulates live track record, this can be increased.
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FILES
# ─────────────────────────────────────────────────────────────────────────────
#
#   research_data/AVAX_ATOM_trade_log.csv
#       → Every trade: entry time, exit time, entry z, exit z, P&L, exit reason
#
#   research_data/AVAX_ATOM_strategy_summary.csv
#       → Aggregate statistics: total trades, win rate, Sharpe, max drawdown
#
#   research_data/AVAX_ATOM_equity_curve.csv
#       → Cumulative P&L curve over time for visual inspection
#
#   research_data/AVAX_ATOM_signal_log.csv
#       → Every candle: signal value, position status, position size
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations              # Modern type hints in Python 3.8+

import sys                                      # claude code changed: new import — needed for the stdout encoding fix below
import logging                                  # Structured logging — same as all modules
import re                                       # claude code changed: new import — needed for _parse_pair_from_kalman_filename()
import warnings                                 # Suppress pandas non-critical warnings
from dataclasses import dataclass, field        # Clean trade record structure
from pathlib import Path                        # Cross-platform file paths
from typing import Dict, List, Optional, Tuple  # Type annotations

import numpy as np                              # Numerical operations
import pandas as pd                             # DataFrame operations
from scipy import stats                         # IC calculation for strategy validation

# claude code changed: new import — the per-trade relative stop needs to cap
# at the same ceiling kalman_zscore itself is winsorised to, otherwise a stop
# level could be computed beyond a value the z-score can ever actually report
from bot.research.kalman_filter_engine import ZSCORE_WINSOR_LIMIT

# claude code changed: new import — Phase 1C, Blocker A/Step 3. Routes this
# engine's transaction costs through the same asset-class-aware cost-model
# boundary bot/config/cost_model.py already established (Phase 1A, STEP 8),
# instead of the crypto-only FEE_RATE/SLIPPAGE_RATE module constants below.
# See CostModel/get_cost_model usage in EntryExitEngine.__init__.
from bot.config.cost_model import CostModel, UnsupportedAssetClassCostModel, get_cost_model
from bot.instruments import ASSET_CLASS_CRYPTO

warnings.filterwarnings('ignore')               # Suppress non-critical warnings

# claude code changed: new — same fix bot_runner.py/health_check.py/
# deep_health_check.py already have. _print_performance_report() uses plain
# print() with a "→" arrow character, which Windows' default console
# codepage (cp1252) can't encode — without this, a run crashes with
# UnicodeEncodeError immediately after all outputs (trade log, summary,
# equity curve) are already saved, so the data survives but the report
# itself never displays. Found while re-running this engine after the P&L
# formula fix.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING — same format as all other QuantiBot Pro modules
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)            # Module-level logger

if not logger.handlers:                         # Prevent duplicate handlers on re-import
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT-SPECIFIC CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
# All parameters below were derived from our research results.
# None of these numbers are guesses — each has a specific derivation
# documented in the comment above it.

# ── Pair identity ─────────────────────────────────────────────────────────────
# This engine is built specifically for the AVAX/ATOM pair
# discovered by cointegration_engine.py and validated by kalman_filter_engine.py
SYMBOL_A: str   = "AVAX_USDT"    # The asset we go long or short
SYMBOL_B: str   = "ATOM_USDT"    # The hedge asset
PAIR_NAME: str  = "AVAX_USDT/ATOM_USDT"

# Validated research results from our pipeline (do not change without re-running research)
VALIDATED_IC:        float = 0.5245    # IC confirmed by feature_validator
VALIDATED_WIN_RATE:  float = 0.6900    # Average win rate across 2020-2025
VALIDATED_HALF_LIFE: float = 119.9     # Half-life in hours from cointegration_engine

# ── Entry rules ───────────────────────────────────────────────────────────────

# Primary entry threshold — z-score must exceed this to consider entering
# Derived from: 2,470 candles had |z| > 2.0 over 5 years = ~10 signals/week
# At IC = 0.5245, all signals above 2.0 have sufficient edge after costs
ENTRY_ZSCORE_THRESHOLD: float = 2.0

# Strong signal threshold — higher confidence entries
# When |z| > 3.0, position can be sized more aggressively
# Only 0.8% of candles exceed this — used for maximum conviction entries
ENTRY_ZSCORE_STRONG: float = 3.0

# Confirmation: the previous candle's z-score must agree with the current one
# Derived from: kalman_zscore_lag1 showed IC = 0.3905 (STRONG KEEP)
# This filter prevents entering on single-candle manipulation spikes
# True = require lag confirmation. False = enter on any z-score breach
REQUIRE_LAG_CONFIRMATION: bool = True

# Minimum pair_signal_dynamic value required alongside z-score
# This is the composite signal (inverted z-score + momentum) from kalman engine
# We require it to agree with the z-score direction for belt-and-suspenders
ENTRY_SIGNAL_MIN: float = 0.3    # pair_signal_dynamic must exceed this

# Prediction error filter — from Kalman engine's prediction_error column
# When prediction_error is unusually large, the filter is uncertain
# We reduce position size (not eliminate entry) when error is elevated
PRED_ERROR_NORMAL: float = 0.02  # Standard deviation of prediction_error = 0.016

# ── Exit rules ────────────────────────────────────────────────────────────────

# Profit target: exit when z-score reverts to near zero
# Derived from: spread mean-reverts to zero by Kalman construction
# Taking profit at ±0.3 captures most of the reversion without chasing the last bit
# claude code changed: kept for backward-compat display/logging only — no
# longer triggers exits, see EXIT_TARGET_OVERSHOOT_FRACTION below for what
# replaced it (same "kept for display, replaced by a per-trade value"
# pattern already used for EXIT_ZSCORE_STOPLOSS just below).
EXIT_ZSCORE_TARGET: float = 0.3

# claude code changed: new — entry-depth-scaled exit target, replacing the
# fixed EXIT_ZSCORE_TARGET above as what actually triggers a TARGET exit.
#
# Why the fixed target was a problem (research_data/model_governance_log.md,
# "Why entry_ic won't move" + "P&L normalisation bug" entries): |z| <= 0.3
# is a wide neighbourhood any mean-reverting z-score wanders into ~30% of
# unconditional time, REGARDLESS of whether the entry that preceded it had
# any real predictive skill — block-shuffling the z-series (which destroys
# genuine temporal/causal structure but preserves this marginal-distribution
# property) still produces win rates/Sharpes this high, which is exactly why
# the permutation test's win_rate_significant/sharpe_significant checks kept
# failing even after fixing the P&L formula and confirming entry_ic is
# genuinely, significantly positive on both tested pairs (0.35 AVAX/ATOM,
# 0.44 DOT/LINK).
#
# This target instead requires the spread to actually CROSS THROUGH
# equilibrium to the OPPOSITE side by an amount scaled to how extreme the
# entry was (target_zscore = -sign(entry_z) * this * |entry_z|, computed
# once per trade at entry — see TradeRecord.target_zscore below, same
# per-trade pattern stop_zscore already uses). Whether a series actually
# completes a full reversion (vs. approaching and reversing back out) is a
# much more temporally/causally dependent event than "wandered near its own
# mean" — the property block-shuffling destroys, not the one it preserves —
# so this is a directed bet that TARGET becomes a rarer, more genuinely
# skill-dependent outcome, converting some of the current ~93-96% win rate
# into STOPLOSS/TIMESTOP exits instead of an near-universal, uninformative
# TARGET hit. 0.10 means a threshold-level entry (|z|=2.0) requires only a
# small confirmed overshoot (0.2) while a deep entry (|z|=4.0) requires
# proportionally more (0.4) — calibrating the confirmation bar to the size
# of the claimed edge, not a fixed absolute distance for every entry.
#
# This is a first design iteration, not a validated result — it needs the
# same full audit (entry/exit sim -> walk-forward -> permutation test) any
# other pair/rule change in this log gets before being trusted.
EXIT_TARGET_OVERSHOOT_FRACTION: float = 0.10

# claude code changed: new — second design iteration
# (research_data/model_governance_log.md, "First entry-depth-scaled exit
# design attempt"). The sign-crossing overshoot above did not fix
# win_rate/sharpe significance: empirically, win rate and Sharpe went UP
# (not down) under it, and 0 trades hit the 240h time-stop, both pointing
# to the same cause — this z-series crosses back through its own rolling
# mean so often (median hold 2-9h vs. a 120h half-life) that most "TARGET"
# exits are near-instant whipsaws, not resolved reversion. This constant
# blocks TARGET from firing until a trade has been held at least this many
# hours — STOPLOSS and TIMESTOP are NOT gated by this (real risk should
# never be suppressed while "waiting"; only the profit-taking exit is
# delayed). The bet: forcing genuinely fast whipsaws to stay exposed to
# stop risk for longer, instead of banking them as instant wins, should
# pull win_rate/Sharpe away from "how often does this series wander near
# its mean in the first few hours" (preserved by block-shuffling) and
# toward "did the position survive long enough to reflect real,
# sustained reversion" (destroyed by block-shuffling, closer to what
# entry_ic already measures). 24h is a first value: long enough that most
# of the current 2-9h median-hold trades would be forced past it, short
# enough to remain a small fraction of the 120h half-life / 240h
# time-stop. Needs the same full audit as every other change in this log.
EXIT_MIN_HOLD_HOURS: float = 24.0

# claude code changed: new — third design iteration
# (research_data/model_governance_log.md, "Second exit design attempt —
# minimum holding period"). Both prior levers changed WHEN a trade could
# exit while still scanning the z-score candle-by-candle at its native 1h
# resolution. This instead changes the RESOLUTION the entry/exit rules
# observe: resample the already-validated 1h kalman_zscore series to a
# lower frequency (taking the last 1h reading in each window) BEFORE
# scanning for entries/exits at all, so a decision only sees a fresh
# reading every N hours instead of every 1h tick. Tests whether
# microstructure noise the original half-life estimate (~120-197h) never
# assumed the rules would react to candle-by-candle is inflating trade
# count/whipsaw, as distinct from the target/stop zone occupancy asymmetry
# (already diagnosed and separately confirmed not fixable via exit-rule
# tweaks alone). None = no resampling (default, fully backward compatible).
# Not yet validated — needs the same full audit as every other change here.
CANDLE_RESAMPLE_HOURS: Optional[int] = None

# claude code changed: new — fourth design iteration (research_data/model_governance_log.md,
# "Third design attempt — lower candle resolution"). Three independent levers (a
# distance-based confirmation, a fixed 24h time-based confirmation, and an
# observation-frequency change) each produced only noise-level movement in the
# permutation test, never replicating across both pairs. What none of them did:
# scale the CONFIRMATION BAR ITSELF continuously with how extreme the entry was.
# EXIT_MIN_HOLD_HOURS=24.0 (design 2) applied the SAME 24h floor to a
# threshold-level entry (|z|=2.0) and a near-winsor entry (|z|=5.0) alike, even
# though entry_ic's own evidence is that deeper entries carry more claimed edge
# and (per the "Why entry_ic won't move" investigation) travel further in real
# terms — a fixed time bar lets a deep entry's bigger target get satisfied by an
# equally-fast random swing as a shallow entry's smaller one. This ties the
# MINIMUM HOLD directly to entry depth (the same |entry_z| that drives
# entry_ic), so confirmation difficulty scales with the size of the claimed
# edge: EXIT_MIN_HOLD_BASE_HOURS at the entry threshold itself
# (|z|=ENTRY_ZSCORE_THRESHOLD=2.0), growing linearly for deeper entries
# (min_hold_hours = this * |entry_z| / ENTRY_ZSCORE_THRESHOLD — e.g. |z|=3.0 ->
# 36h, |z|=5.0 -> 60h at the default below). Combined with the existing
# entry-depth-scaled sign-crossing TARGET (design 1, unchanged) — both the
# distance AND the time required to claim a win now scale with entry depth,
# not just one of the two. 24.0 keeps the threshold-entry bar identical to
# design 2's flat value, isolating "does scaling by depth help" from "is a
# lower average bar the real driver." Supersedes EXIT_MIN_HOLD_HOURS as what
# actually triggers TARGET — that constant is kept for backward-compat display
# only, same pattern as EXIT_ZSCORE_TARGET/EXIT_ZSCORE_STOPLOSS below. Not yet
# validated — needs the same full audit as every other change in this log.
EXIT_MIN_HOLD_BASE_HOURS: float = 24.0

# claude code changed: EXIT_ZSCORE_STOPLOSS kept for backward-compat display only
# (constructor param / summary dict) — it no longer triggers exits. A FIXED
# absolute stop level meant every trade's "room before stopping out" shrank
# mechanically as the entry z got more extreme (entry at 2.1 had ~2.4 z-units
# of room; entry at 3.8 had only ~0.7) — this alone produced
# IC(|entry_z| -> is_winner) = -0.20 on BOTH AVAX/ATOM and DOT/LINK (see
# model_governance_log.md), independent of any real market relationship.
# Stop loss: exit if z-score moves further against us beyond this level
# Derived from: Kalman clipped at ±5.0. Above 4.5 almost always means
# structural break or major event — the correct response is to exit
EXIT_ZSCORE_STOPLOSS: float = 4.5

# claude code changed: new — replaces EXIT_ZSCORE_STOPLOSS as the thing that
# actually triggers a stop. Every trade now gets this SAME z-distance of room
# from its OWN entry point (stop_zscore = |entry_z| + this, capped at
# ZSCORE_WINSOR_LIMIT since the z-score can never actually report a value
# beyond its own clip). 1.5 is deliberately smaller than the old 4.5-2.0=2.5
# "designed" distance for a threshold entry — a large relative distance
# applied to an already-extreme entry (e.g. |z|=4.0) would push the stop
# level past the ±5.0 winsor ceiling, i.e. no stop at all for that trade.
# 1.5 keeps the effective stop reachable (and thus actually protective) for
# the large majority of entries instead of silently removing stop protection
# for the most extreme ones.
EXIT_ZSCORE_STOP_DISTANCE: float = 1.5

# Time stop: maximum hours a position can be held
# Derived from: 2 × half-life = 2 × 119.9h = 239.8h ≈ 240 hours
# If spread has not reverted in 10 days, something structural has changed
EXIT_TIME_STOP_HOURS: int = 240

# Partial exit level: close 50% of position when z-score reaches this level
# This locks in partial profit while keeping half the position running
# toward the full target — reduces risk of full reversion being missed
EXIT_PARTIAL_ZSCORE: float = 1.0    # Close 50% of position at z=1.0
EXIT_PARTIAL_FRACTION: float = 0.50 # Fraction to close at partial exit

# ── Position sizing ───────────────────────────────────────────────────────────

# Total capital allocated to this strategy (in USDT)
# This is the research default — override in live trading
# At $10,000 base capital, maximum single position is $10,000
STRATEGY_CAPITAL_USDT: float = 10_000.0

# Kelly fraction safety multiplier
# Full Kelly = IC² / variance of returns (mathematically optimal)
# Quarter-Kelly (0.25) = institutional standard for new strategy deployment
# After 6 months of live track record matching backtest, increase to 0.40
KELLY_SAFETY_FRACTION: float = 0.25

# Maximum position size as fraction of total capital
# Even if Kelly suggests more, never risk more than 30% on one trade
# This prevents ruin from a single cointegration break event
MAX_POSITION_FRACTION: float = 0.30

# Minimum position size — below this, skip the trade (not worth the fee)
MIN_POSITION_USDT: float = 100.0

# claude code changed: FEE_RATE/SLIPPAGE_RATE/TOTAL_TRANSACTION_COST below are
# kept ONLY as the CRYPTO/binance reference values and as the fallback used if
# get_cost_model() ever can't be reached — nothing in this module reads them
# directly anymore. EntryExitEngine.__init__ now resolves costs per-instance
# via bot.config.cost_model.get_cost_model(asset_class, venue_id).get_costs()
# (Phase 1C, Blocker A/Step 3), so a differently-configured engine (a
# different venue, or — once a real cost model exists — a non-crypto asset
# class) gets its OWN fee/slippage rates instead of always this pair's
# Binance-modeled numbers. The default (asset_class=CRYPTO, venue_id=
# "binance") resolves to EXACTLY these same values today (get_venue_execution_
# costs("binance") == {"fee_rate": FEE_RATE, "slippage_rate": SLIPPAGE_RATE}),
# so the AVAX/ATOM baseline is unchanged — see model_governance_log.md for
# the byte-identical regression check.

# Fee rate per side — Binance maker fee for most accounts
# Total round-trip cost = 2 × FEE_RATE
FEE_RATE: float = 0.001    # 0.1% per side = 0.2% round trip

# Slippage estimate — market impact of our orders
# AVAX and ATOM are liquid enough that 0.05% slippage is realistic
SLIPPAGE_RATE: float = 0.0005    # 0.05% per side

# Total transaction cost per trade (entry + exit, both sides)
# Used to calculate minimum z-score where trade is profitable after costs
TOTAL_TRANSACTION_COST: float = 2 * (FEE_RATE + SLIPPAGE_RATE)  # 0.3% round trip

# ── Cointegration health filters ──────────────────────────────────────────────
# These filters pause the strategy when the underlying cointegration
# relationship shows signs of breaking down

# Maximum acceptable β drift per 24 hours
# If the Kalman β changes more than this in one day, the relationship
# is shifting too fast — reduce position sizes as a precaution
MAX_BETA_DRIFT_PER_DAY: float = 0.05    # β should not drift more than 0.05 per day

# Maximum acceptable beta_uncertainty
# When the Kalman filter is very uncertain about β, the spread calculation
# is less reliable — we skip entry when uncertainty is too high
MAX_BETA_UNCERTAINTY: float = 0.10      # Skip entry if beta_uncertainty > 0.10

# Minimum spread z-score standard deviation over rolling window
# If spread std collapses, the pair has stopped diverging meaningfully
# This prevents entering when there are no real trading opportunities
MIN_SPREAD_STD: float = 0.005           # Minimum meaningful spread volatility


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE RECORD DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """
    Stores the complete record of one AVAX/ATOM pairs trade.

    A pairs trade has two legs simultaneously:
        Leg A: position in AVAX_USDT (long or short)
        Leg B: position in ATOM_USDT (opposite direction, sized by β_t)

    The two legs together create a MARKET NEUTRAL position:
        If crypto market crashes 20%, both AVAX and ATOM fall
        The long leg loses. The short leg gains.
        Net exposure to market direction = approximately zero.
        We only profit when AVAX and ATOM CONVERGE toward equilibrium.

    All P&L is measured as spread return — not individual asset return.
    A "winning trade" means the spread moved from its entry z-score
    back toward zero during our holding period.
    """

    # ── Trade identification ───────────────────────────────────────────────────
    trade_id:          int              # Sequential trade number (1, 2, 3...)
    entry_timestamp:   pd.Timestamp     # When we entered the trade
    exit_timestamp:    Optional[pd.Timestamp] = None   # When we exited (None if open)

    # ── Entry state ───────────────────────────────────────────────────────────
    entry_zscore:      float = 0.0      # Kalman z-score at entry (e.g. +2.3)
    entry_signal:      float = 0.0      # pair_signal_dynamic at entry
    entry_beta:        float = 0.0      # Kalman β at entry (dynamic hedge ratio)
    entry_spread:      float = 0.0      # Raw Kalman spread value at entry

    # ── Direction ─────────────────────────────────────────────────────────────
    # Direction describes the SPREAD position, not individual assets
    # LONG_SPREAD  : spread is below equilibrium → expect spread to rise
    #                → in practice: LONG AVAX + SHORT ATOM × β
    # SHORT_SPREAD : spread is above equilibrium → expect spread to fall
    #                → in practice: SHORT AVAX + LONG ATOM × β
    direction:         str = ""         # "LONG_SPREAD" or "SHORT_SPREAD"

    # ── Position sizing ───────────────────────────────────────────────────────
    position_usdt:     float = 0.0      # Total capital allocated (USDT)
    leg_a_usdt:        float = 0.0      # AVAX leg size in USDT
    leg_b_usdt:        float = 0.0      # ATOM leg size in USDT
    kelly_fraction:    float = 0.0      # Kelly fraction used for this trade
    signal_strength:   str   = ""       # "NORMAL" or "STRONG" (|z| > 3.0) — label only, no longer affects size (claude code changed)

    # claude code changed: new field — the ABSOLUTE |z| level THIS trade stops
    # out at, computed once at entry as |entry_zscore| + EXIT_ZSCORE_STOP_DISTANCE
    # (capped at ZSCORE_WINSOR_LIMIT). Replaces checking every trade against the
    # same fixed EXIT_ZSCORE_STOPLOSS regardless of how extreme its entry was.
    stop_zscore:       float = 0.0

    # claude code changed: new field — the signed z-score level THIS trade's
    # profit target requires crossing to, computed once at entry as
    # -sign(entry_zscore) * EXIT_TARGET_OVERSHOOT_FRACTION * |entry_zscore|
    # (see that constant's comment for the full rationale). Replaces checking
    # every trade against the same fixed |z| <= EXIT_ZSCORE_TARGET regardless
    # of how extreme its entry was — same per-trade pattern stop_zscore uses.
    target_zscore:     float = 0.0

    # claude code changed: new field — the number of hours THIS trade must be
    # held before TARGET is allowed to fire, computed once at entry as
    # EXIT_MIN_HOLD_BASE_HOURS * |entry_zscore| / ENTRY_ZSCORE_THRESHOLD (see
    # that constant's module comment for the full rationale). Replaces
    # checking every trade against the same fixed EXIT_MIN_HOLD_HOURS
    # regardless of how extreme its entry was — same per-trade pattern
    # stop_zscore/target_zscore already use.
    min_hold_hours:    float = 0.0

    # ── Exit state ────────────────────────────────────────────────────────────
    exit_zscore:       float = 0.0      # Kalman z-score at exit
    exit_reason:       str   = ""       # "TARGET", "STOPLOSS", "TIMESTOP", "PARTIAL"
    hours_held:        float = 0.0      # How long the trade was open

    # ── Partial exit tracking ───────────────────────────────────────────────────
    # A partial exit realises P&L on part of the position *before* the trade
    # is fully closed. That realised P&L must be folded into the trade's
    # final net P&L — otherwise it is calculated and then silently dropped.
    partial_pnl_usdt:   float = 0.0      # Realised $ P&L from the partial exit (if any)
    partial_exit_zscore: Optional[float] = None   # z-score at the moment of partial exit

    # ── P&L ───────────────────────────────────────────────────────────────────
    # All P&L measured as log-spread return (consistent with Kalman units)
    gross_pnl_pct:     float = 0.0      # P&L before fees and slippage
    fee_cost_pct:      float = 0.0      # Total transaction costs (both legs)
    net_pnl_pct:       float = 0.0      # P&L after all costs
    net_pnl_usdt:      float = 0.0      # Net P&L in USDT terms

    # ── Trade quality metrics ──────────────────────────────────────────────────
    max_adverse_zscore: float = 0.0     # Worst z-score move against us (drawdown)
    max_favorable_zscore: float = 0.0   # Best z-score move in our favour (MFE)
    prediction_error_at_entry: float = 0.0   # Kalman prediction error at entry
    beta_uncertainty_at_entry: float = 0.0   # Kalman β uncertainty at entry

    def to_dict(self) -> Dict:
        """Convert trade record to dictionary for DataFrame construction."""
        return {
            "trade_id":                    self.trade_id,
            "entry_timestamp":             self.entry_timestamp,
            "exit_timestamp":              self.exit_timestamp,
            "entry_zscore":                round(self.entry_zscore, 4),
            "entry_signal":                round(self.entry_signal, 4),
            "entry_beta":                  round(self.entry_beta, 4),
            "entry_spread":                round(self.entry_spread, 6),
            "direction":                   self.direction,
            "position_usdt":               round(self.position_usdt, 2),
            "leg_a_usdt":                  round(self.leg_a_usdt, 2),
            "leg_b_usdt":                  round(self.leg_b_usdt, 2),
            "kelly_fraction":              round(self.kelly_fraction, 4),
            "signal_strength":             self.signal_strength,
            "stop_zscore":                 round(self.stop_zscore, 4),   # claude code changed: new
            "target_zscore":               round(self.target_zscore, 4),   # claude code changed: new
            "min_hold_hours":              round(self.min_hold_hours, 2),   # claude code changed: new
            "exit_zscore":                 round(self.exit_zscore, 4),
            "exit_reason":                 self.exit_reason,
            "hours_held":                  round(self.hours_held, 1),
            "partial_pnl_usdt":            round(self.partial_pnl_usdt, 2),
            "gross_pnl_pct":               round(self.gross_pnl_pct, 6),
            "fee_cost_pct":                round(self.fee_cost_pct, 6),
            "net_pnl_pct":                 round(self.net_pnl_pct, 6),
            "net_pnl_usdt":                round(self.net_pnl_usdt, 2),
            "max_adverse_zscore":          round(self.max_adverse_zscore, 4),
            "max_favorable_zscore":        round(self.max_favorable_zscore, 4),
            "prediction_error_at_entry":   round(self.prediction_error_at_entry, 6),
            "beta_uncertainty_at_entry":   round(self.beta_uncertainty_at_entry, 6),
            "is_winner":                   self.net_pnl_pct > 0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION SIZER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class KalmanPositionSizer:
    """
    Kelly Criterion position sizer calibrated to the AVAX/ATOM Kalman strategy.

    The Kelly Criterion answers: given a known edge (IC) and known variance
    of returns, what fraction of capital should I risk on each trade?

    Full Kelly formula for a continuous return strategy:
        kelly_fraction = IC / (std_of_returns / mean_spread_move)

    In practice for our strategy:
        kelly_fraction = (win_rate - loss_rate) / win_loss_ratio

    We apply a 25% safety fraction (quarter-Kelly) as the institutional
    standard for a strategy in its first deployment phase.

    Position sizing also accounts for:
        Beta uncertainty: higher uncertainty → smaller position
        Prediction error: elevated filter error → smaller position

    claude code changed: dropped "stronger signals (|z| > 3) get larger
    positions" — audit on real trade logs found STRONG signals (|z| >= 3.0)
    have a LOWER win rate than NORMAL on both AVAX/ATOM and DOT/LINK, so
    sizing them up was backwards. See size_position()'s Step 2 comment.
    """

    def __init__(
        self,
        capital_usdt:         float = STRATEGY_CAPITAL_USDT,
        kelly_safety:         float = KELLY_SAFETY_FRACTION,
        max_position_fraction: float = MAX_POSITION_FRACTION,
        validated_ic:         float = VALIDATED_IC,
        validated_win_rate:   float = VALIDATED_WIN_RATE,
    ) -> None:
        """
        Initialise the position sizer with strategy parameters.

        Parameters
        ----------
        capital_usdt : float
            Total capital allocated to this strategy.

        kelly_safety : float
            Safety multiplier on full Kelly (0.25 = quarter-Kelly).

        max_position_fraction : float
            Hard cap on single position as fraction of total capital.

        validated_ic : float
            IC confirmed by feature_validator_v2_institutional.py (0.5245).

        validated_win_rate : float
            Average win rate across 2020-2025 (0.69).
        """

        self.capital         = capital_usdt          # Total strategy capital
        self.kelly_safety    = kelly_safety           # Quarter-Kelly safety factor
        self.max_fraction    = max_position_fraction  # Hard position cap
        self.ic              = validated_ic           # 0.5245 from our research
        self.win_rate        = validated_win_rate     # 0.69 from stability analysis

        # Compute base Kelly fraction from our validated win rate
        # Kelly formula: f = win_rate - (1 - win_rate) / win_loss_ratio
        # For mean-reversion pairs trading: win_loss_ratio ≈ win_rate / (1-win_rate)
        # claude code changed: real algebra error found and fixed during
        # Hardening Phase 2's independent metric re-derivation
        # (bot/tests/test_analytics_independent_verification.py). The
        # CODE below was always correct; this COMMENT's claimed
        # simplification was not: substituting win_loss_ratio =
        # win_rate/loss_rate into f = win_rate - loss_rate/win_loss_ratio
        # gives f* = win_rate - loss_rate^2/win_rate = (2*win_rate - 1) /
        # win_rate — NOT "2*win_rate - 1". At win_rate=0.69 the correct
        # simplification gives 0.5507 (matching every real logged "Full
        # Kelly" value this project has ever produced); "2*win_rate - 1"
        # would give 0.38, a number that has never actually appeared in
        # this codebase's real output. "2p-1" is the correct Kelly
        # simplification only for an even-money bet (win_loss_ratio
        # fixed at 1), which is a different assumption than the one this
        # class actually uses.
        # This gives: f* = (2 × win_rate - 1) / win_rate = (2×0.69 - 1) / 0.69 = 0.5507
        loss_rate         = 1.0 - self.win_rate          # 0.31
        win_loss_ratio    = self.win_rate / loss_rate     # 0.69/0.31 = 2.22
        self.base_kelly   = (
            self.win_rate - loss_rate / win_loss_ratio    # Full Kelly fraction
        )
        # Apply safety multiplier — quarter-Kelly for new strategy deployment
        self.safe_kelly   = self.base_kelly * self.kelly_safety

        logger.info("KalmanPositionSizer initialised")
        logger.info(f"  Capital           : ${capital_usdt:,.0f}")
        logger.info(f"  Validated IC      : {validated_ic}")
        logger.info(f"  Validated win rate: {validated_win_rate:.1%}")
        logger.info(f"  Full Kelly        : {self.base_kelly:.4f} ({self.base_kelly:.1%})")
        logger.info(f"  Safe Kelly (25%)  : {self.safe_kelly:.4f} ({self.safe_kelly:.1%})")
        logger.info(f"  Max single position: {max_position_fraction:.0%} of capital")
        logger.info(
            f"  Max position size : "
            f"${capital_usdt * max_position_fraction:,.0f}"
        )

    def size_position(
        self,
        zscore:           float,
        beta:             float,
        beta_uncertainty: float,
        prediction_error: float,
    ) -> Tuple[float, float, float, str]:
        """
        Calculate the position size for one trade.

        Takes four inputs from the Kalman engine output:
            zscore           — how extreme is the current divergence?
            beta             — current dynamic hedge ratio (for leg sizing)
            beta_uncertainty — how confident is the Kalman in this β?
            prediction_error — how wrong was the filter recently?

        Returns
        -------
        Tuple of:
            total_usdt    : total capital to deploy on this trade
            leg_a_usdt    : AVAX leg size in USDT
            leg_b_usdt    : ATOM leg size in USDT
            signal_strength: "NORMAL" or "STRONG"
        """

        # ── Step 1: Base Kelly position ───────────────────────────────────────
        # Start with the safe Kelly fraction of total capital
        # This is the baseline position before any adjustments
        kelly_usdt = self.capital * self.safe_kelly

        # ── Step 2: Signal strength label (size no longer varies with it) ────
        # claude code changed: was sizing STRONG (|z|>=3.0) trades 1.5x normal,
        # on the stated rationale that "higher z-scores have higher win rates."
        # Audit on real trade logs (AVAX/ATOM and DOT/LINK, both) found the
        # opposite: STRONG trades had a LOWER win rate than NORMAL in both
        # (83.5% vs 95.5% on AVAX/ATOM; 79.8% vs 93.8% on DOT/LINK) — because a
        # bigger entry |z| is mechanically closer to a fixed stop level (see
        # EXIT_ZSCORE_STOPLOSS/EXIT_ZSCORE_STOP_DISTANCE comments), not because
        # it carries more genuine edge. Sizing UP the bucket that performs
        # WORSE was actively harmful, independent of whether the underlying
        # signal has any real predictive power at all. Every entry now gets
        # the same size; signal_strength is kept only as a label for analysis.
        signal_multiplier = 1.00
        signal_strength   = "STRONG" if abs(zscore) >= ENTRY_ZSCORE_STRONG else "NORMAL"

        # ── Step 3: Beta uncertainty adjustment ───────────────────────────────
        # When the Kalman filter is uncertain about β (high beta_uncertainty),
        # the spread calculation is less reliable — reduce position size
        # This is unique to the Kalman approach — OLS cannot do this
        # because it has no concept of dynamic uncertainty
        if beta_uncertainty > MAX_BETA_UNCERTAINTY:
            # Uncertainty is too high — halve the position
            uncertainty_multiplier = 0.50
            logger.debug(
                f"    Beta uncertainty {beta_uncertainty:.4f} > "
                f"{MAX_BETA_UNCERTAINTY} — halving position"
            )
        elif beta_uncertainty > MAX_BETA_UNCERTAINTY * 0.5:
            # Moderate uncertainty — reduce slightly
            uncertainty_multiplier = 0.75
        else:
            # Low uncertainty — Kalman is confident, full position
            uncertainty_multiplier = 1.00

        # ── Step 4: Prediction error adjustment ──────────────────────────────
        # When the Kalman filter's prediction error is unusually large,
        # the filter is surprised by recent price action — possible regime shift
        # We reduce position size when the filter is in an uncertain state
        pred_error_abs = abs(prediction_error)
        if pred_error_abs > PRED_ERROR_NORMAL * 3:
            # Filter is very surprised — reduce to 60% of normal size
            error_multiplier = 0.60
        elif pred_error_abs > PRED_ERROR_NORMAL * 2:
            # Filter is somewhat surprised — reduce to 80%
            error_multiplier = 0.80
        else:
            # Normal prediction error — full size
            error_multiplier = 1.00

        # ── Step 5: Compute final position size ───────────────────────────────
        # Combine all adjustments multiplicatively
        adjusted_usdt = (
            kelly_usdt *
            signal_multiplier *
            uncertainty_multiplier *
            error_multiplier
        )

        # Apply hard cap: never exceed MAX_POSITION_FRACTION of total capital
        # This prevents ruin from a single cointegration break event
        max_usdt      = self.capital * self.max_fraction
        total_usdt    = min(adjusted_usdt, max_usdt)

        # Apply minimum position check: below MIN_POSITION_USDT, skip the trade
        if total_usdt < MIN_POSITION_USDT:
            return 0.0, 0.0, 0.0, signal_strength   # Signal: do not trade

        # ── Step 6: Split into two legs using Kalman β ────────────────────────
        # The hedge ratio β determines how to split capital between AVAX and ATOM
        # For a dollar-neutral position:
        #   Total capital = leg_A + leg_B
        #   leg_B / leg_A = β_t (the current Kalman hedge ratio)
        # Therefore:
        #   leg_A = total / (1 + β_t)
        #   leg_B = total × β_t / (1 + β_t)
        # This makes the position dollar-neutral in log-price space
        # Any market-wide crypto move affects both legs proportionally
        leg_a_usdt = total_usdt / (1.0 + beta)          # AVAX leg
        leg_b_usdt = total_usdt * beta / (1.0 + beta)   # ATOM leg

        return total_usdt, leg_a_usdt, leg_b_usdt, signal_strength


# claude code changed: new section — whole block below (module constant + function) added for the pair-agnostic refactor
# ═══════════════════════════════════════════════════════════════════════════════
# PAIR IDENTITY — parsed from the Kalman CSV filename, not hardcoded
# ═══════════════════════════════════════════════════════════════════════════════
# kalman_filter_engine.py names its output "<SYMBOL_A>_<SYMBOL_B>_kalman.csv"
# (e.g. "AVAX_USDT_ATOM_USDT_kalman.csv"). Parsing pair identity back out of
# that filename lets this module run on whichever pair's CSV it's pointed
# at, instead of only ever knowing about AVAX/ATOM.
_KALMAN_FILENAME_RE = re.compile(r"^(.+?_USDT)_(.+_USDT)_kalman\.csv$")   # claude code changed: new — matches "<A>_<B>_kalman.csv"


def _parse_pair_from_kalman_filename(kalman_csv: str) -> Tuple[str, str, str]:   # claude code changed: new function
    """
    Parse (pair_name, symbol_a, symbol_b) out of a kalman_filter_engine.py
    output filename, e.g.:
        "AVAX_USDT_ATOM_USDT_kalman.csv"
            -> ("AVAX_USDT/ATOM_USDT", "AVAX_USDT", "ATOM_USDT")

    Raises ValueError if the filename doesn't follow that convention. Scratch
    files that legitimately don't (a walk-forward fold's "train_slice_kalman
    .csv", a permutation test's "shuffled_kalman.csv") are the caller's
    responsibility to catch and fall back on explicitly — this function
    never guesses.
    """
    # claude code changed: entire function body below is new (docstring above can't carry a "#" marker without corrupting it)
    match = _KALMAN_FILENAME_RE.match(Path(kalman_csv).name)             # claude code changed: new
    if not match:                                                         # claude code changed: new
        raise ValueError(                                                 # claude code changed: new
            f"'{Path(kalman_csv).name}' does not follow the "             # claude code changed: new
            f"'<SYMBOL_A>_<SYMBOL_B>_kalman.csv' naming convention "      # claude code changed: new
            f"kalman_filter_engine.py uses for real pair output "         # claude code changed: new
            f"(e.g. 'AVAX_USDT_ATOM_USDT_kalman.csv')."                   # claude code changed: new
        )                                                                  # claude code changed: new
    symbol_a, symbol_b = match.group(1), match.group(2)                  # claude code changed: new
    return f"{symbol_a}/{symbol_b}", symbol_a, symbol_b                  # claude code changed: new


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY EXIT ENGINE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class EntryExitEngine:
    """
    Entry and exit rules engine for the AVAX/ATOM Kalman pairs strategy.

    This engine reads the Kalman filter output CSV and simulates the complete
    trading strategy — scanning every candle, entering trades when conditions
    are met, and exiting when profit target, stop loss, or time stop is reached.

    This is a RESEARCH SIMULATION engine, not a live trading engine.
    It produces a trade log that feeds into walk_forward_engine.py (Missing Piece 5).

    The distinction is critical:
        EntryExitEngine  → simulates strategy on historical data
        [Future] execution_engine.py → places real orders on Binance

    This separation follows the same research-first philosophy as the rest
    of the QuantiBot Pro pipeline: prove the edge in simulation before
    touching real capital.

    Integration:
        INPUT:  research_data/AVAX_USDT_ATOM_USDT_kalman.csv
                (produced by kalman_filter_engine.py)
        OUTPUT: research_data/AVAX_ATOM_trade_log.csv
                research_data/AVAX_ATOM_strategy_summary.csv
                research_data/AVAX_ATOM_equity_curve.csv
                research_data/AVAX_ATOM_signal_log.csv
    """

    def __init__(
        self,
        entry_threshold:       float = ENTRY_ZSCORE_THRESHOLD,
        entry_strong:          float = ENTRY_ZSCORE_STRONG,
        exit_target:           float = EXIT_ZSCORE_TARGET,           # claude code changed: kept for backward-compat display only, see module constant comment
        exit_target_overshoot: float = EXIT_TARGET_OVERSHOOT_FRACTION,   # claude code changed: new param — this is what actually triggers a TARGET exit now
        exit_min_hold_hours:   float = EXIT_MIN_HOLD_HOURS,          # claude code changed: kept for backward-compat display only — see EXIT_MIN_HOLD_BASE_HOURS module comment
        exit_min_hold_base_hours: float = EXIT_MIN_HOLD_BASE_HOURS,  # claude code changed: new param — this is what actually gates TARGET now, scaled per-trade by entry depth
        resample_hours:        Optional[int] = CANDLE_RESAMPLE_HOURS,   # claude code changed: new param — resample the kalman series to this candle width before scanning; None = no resampling
        exit_stoploss:         float = EXIT_ZSCORE_STOPLOSS,        # claude code changed: kept for backward-compat display only, see module constant comment
        exit_stop_distance:    float = EXIT_ZSCORE_STOP_DISTANCE,   # claude code changed: new param — this is what actually triggers a stop now
        exit_time_stop_hours:  int   = EXIT_TIME_STOP_HOURS,
        exit_partial_zscore:   float = EXIT_PARTIAL_ZSCORE,
        exit_partial_fraction: float = EXIT_PARTIAL_FRACTION,
        require_confirmation:  bool  = REQUIRE_LAG_CONFIRMATION,
        capital_usdt:          float = STRATEGY_CAPITAL_USDT,
        kelly_safety:          float = KELLY_SAFETY_FRACTION,
        output_dir:            str   = "research_data",
        pair_name:             Optional[str]   = None,   # claude code changed: new param
        symbol_a:              Optional[str]   = None,   # claude code changed: new param
        symbol_b:              Optional[str]   = None,   # claude code changed: new param
        validated_half_life:   Optional[float] = None,   # claude code changed: new param
        validated_ic:          Optional[float] = None,   # claude code changed: new param
        validated_win_rate:    Optional[float] = None,   # claude code changed: new param
        asset_class:           str             = ASSET_CLASS_CRYPTO,   # claude code changed: new param — Phase 1C, Blocker A/Step 3
        venue_id:               str             = "binance",            # claude code changed: new param — Phase 1C, Blocker A/Step 3
        cost_model:             Optional[CostModel] = None,             # claude code changed: new param — Phase 1C, Blocker A/Step 3
    ) -> None:
        """
        Initialise the entry/exit engine with all strategy parameters.

        Every rule parameter (entry_threshold, exit_target, ...) has a
        research-derived default. Override only if you have evidence from
        the walk-forward validation that a different value improves
        out-of-sample performance.

        pair_name / symbol_a / symbol_b : Optional[str]
            Pair identity. Leave unset and call run(kalman_csv=...) — it
            will parse these from the kalman CSV's filename. Pass them
            explicitly only when the CSV won't have a parseable filename
            (e.g. a caller writing its own scratch slice).

        validated_half_life / validated_ic / validated_win_rate : Optional[float]
            Pre-existing "known good" baselines for THIS pair, used only
            for the sanity-check/reporting in _build_strategy_summary() and
            _print_performance_report() — never for position sizing. There
            is no cross-pair source of truth for these, so they default to
            None ("not yet validated") rather than silently reusing
            AVAX/ATOM's VALIDATED_IC / VALIDATED_WIN_RATE, which
            research_data/model_governance_log.md has since shown were
            never actually valid even for AVAX/ATOM itself.

        asset_class / venue_id : str
            Which bot.config.cost_model.get_cost_model() entry to resolve
            fee_rate/slippage_rate from. Defaults to CRYPTO/"binance" —
            the AVAX/ATOM reference pair's real venue — which resolves to
            the exact same numeric values the old FEE_RATE/SLIPPAGE_RATE
            module constants held, so existing callers see byte-identical
            costs unless they explicitly ask for something else. Raises
            bot.config.cost_model.UnsupportedAssetClassCostModel if asked
            for an asset class with no real cost model yet (e.g. US_EQUITY,
            FOREX) — this engine never guesses a crypto-shaped cost for a
            market that has never had one.

        cost_model : Optional[CostModel]
            Escape hatch to inject a CostModel instance directly instead of
            resolving one from asset_class/venue_id — e.g. for tests, or a
            future caller with its own already-resolved cost model. When
            given, asset_class/venue_id are ignored.
        """
        # claude code changed: the six "pair_name / symbol_a / ... validated_win_rate" paragraphs above are a new docstring addition (no "#" possible inside the string itself); the asset_class/venue_id/cost_model paragraphs are also new, same reason

        # Store all strategy parameters
        self.entry_threshold   = entry_threshold       # |z| must exceed this to enter
        self.entry_strong      = entry_strong           # |z| for strong signal classification
        self.exit_target       = exit_target            # claude code changed: no longer used to trigger exits — kept for backward-compat display (summary dict, logging) only
        self.exit_target_overshoot = exit_target_overshoot   # claude code changed: new — actual per-trade target is -sign(entry_z) * this * |entry_z|, see TradeRecord.target_zscore
        self.exit_stoploss     = exit_stoploss          # claude code changed: no longer used to trigger exits — kept for backward-compat display (summary dict, logging) only
        self.exit_stop_distance = exit_stop_distance    # claude code changed: new — actual per-trade stop is |entry_z| + this, see TradeRecord.stop_zscore
        self.exit_min_hold_hours = exit_min_hold_hours  # claude code changed: no longer used to trigger exits — kept for backward-compat display only, see EXIT_MIN_HOLD_BASE_HOURS module comment
        self.exit_min_hold_base_hours = exit_min_hold_base_hours  # claude code changed: new — actual per-trade min hold is this * |entry_z| / entry_threshold, see TradeRecord.min_hold_hours
        self.resample_hours    = resample_hours         # claude code changed: new — see CANDLE_RESAMPLE_HOURS module comment; applied once in _load_kalman_data()
        self.exit_time_stop    = exit_time_stop_hours   # Maximum hours to hold position
        self.exit_partial_z    = exit_partial_zscore    # z-score for partial exit
        self.exit_partial_frac = exit_partial_fraction  # Fraction to close at partial exit
        self.require_confirm   = require_confirmation   # Require lag z-score confirmation
        self.output_dir        = Path(output_dir)       # Where to save results
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # claude code changed: new block — Phase 1C, Blocker A/Step 3. Resolve
        # this engine's OWN fee_rate/slippage_rate through the cost-model
        # boundary instead of reading the module-level FEE_RATE/SLIPPAGE_RATE
        # crypto/Binance constants directly. Raises UnsupportedAssetClassCostModel
        # (not caught here) if asset_class has no real cost model yet — fail
        # loud at construction, not with a silently wrong number mid-simulation.
        self.asset_class = asset_class                                            # claude code changed: new
        self.venue_id     = venue_id                                              # claude code changed: new
        resolved_cost_model = cost_model if cost_model is not None else get_cost_model(asset_class, venue_id)  # claude code changed: new
        _costs = resolved_cost_model.get_costs()                                 # claude code changed: new
        self.fee_rate      = _costs["fee_rate"]                                  # claude code changed: new
        self.slippage_rate = _costs["slippage_rate"]                             # claude code changed: new
        self.total_transaction_cost = 2 * (self.fee_rate + self.slippage_rate)   # claude code changed: new — replaces module-level TOTAL_TRANSACTION_COST at every use site below

        # claude code changed: new block (6 lines below) — stores the new pair-identity/validated-baseline params
        # Pair identity — resolved here if given, otherwise by run() from
        # the kalman CSV filename (see _parse_pair_from_kalman_filename)
        self.pair_name           = pair_name             # claude code changed: new
        self.symbol_a             = symbol_a             # claude code changed: new
        self.symbol_b             = symbol_b             # claude code changed: new
        self.validated_half_life = validated_half_life  # claude code changed: new — set by run_entry_exit_simulation() via load_pair_config()
        self.validated_ic        = validated_ic         # claude code changed: new — left unset unless a caller supplies a pair-specific value
        self.validated_win_rate  = validated_win_rate   # claude code changed: new — left unset unless a caller supplies a pair-specific value

        # Initialise position sizer with our validated research parameters
        self.sizer = KalmanPositionSizer(
            capital_usdt=capital_usdt,
            kelly_safety=kelly_safety,
        )

        # State tracking — these update as we scan through candles
        self.current_trade:    Optional[TradeRecord] = None    # Active trade (if any)
        self.trade_id_counter: int                   = 0       # Sequential trade counter
        self.completed_trades: List[TradeRecord]     = []      # All finished trades
        self.partial_exited:   bool                  = False   # Has partial exit fired?

        half_life_note = (                                                              # claude code changed: new — was hardcoded VALIDATED_HALF_LIFE below
            f"{self.validated_half_life}h" if self.validated_half_life is not None      # claude code changed: new
            else "unresolved — set by run() or run_entry_exit_simulation()"             # claude code changed: new
        )                                                                                # claude code changed: new
        logger.info("EntryExitEngine initialised")
        logger.info(f"  Pair              : {self.pair_name or '(resolved from kalman CSV filename at run())'}")  # claude code changed: was hardcoded f"{PAIR_NAME}"
        logger.info(f"  Entry threshold   : |z| > {entry_threshold}")
        logger.info(f"  Strong threshold  : |z| > {entry_strong}")
        logger.info(f"  Exit target       : cross to -sign(entry_z) * {exit_target_overshoot} * |entry_z| (per-trade, entry-depth-scaled)")
        logger.info(f"  Min hold for target: {exit_min_hold_base_hours}h at |z|={entry_threshold} threshold, scaling linearly with entry depth (STOPLOSS/TIMESTOP unaffected)")   # claude code changed: was a flat exit_min_hold_hours value
        logger.info(f"  Candle resolution : {resample_hours}h (resampled)" if resample_hours else "  Candle resolution : native (no resampling)")   # claude code changed: new
        logger.info(f"  Stop loss         : |entry_z| + {exit_stop_distance} (per-trade, capped at ±{ZSCORE_WINSOR_LIMIT})")   # claude code changed: was f"|z| > {exit_stoploss}"
        logger.info(f"  Time stop         : {exit_time_stop_hours}h "                   # claude code changed: message reworded around half_life_note
                    f"(reference half-life: {half_life_note})")                          # claude code changed: was f"(2 x half-life {VALIDATED_HALF_LIFE}h)"
        logger.info(f"  Partial exit      : 50% at |z| < {exit_partial_zscore}")
        logger.info(f"  Lag confirmation  : {require_confirmation}")
        logger.info(f"  Cost model        : {asset_class}/{venue_id} — fee {self.fee_rate:.4%} + slippage {self.slippage_rate:.4%} per side")   # claude code changed: was hardcoded module-constant read
        logger.info(f"  Transaction cost  : {self.total_transaction_cost:.2%} round trip")   # claude code changed: was TOTAL_TRANSACTION_COST module constant


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: run()
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        kalman_csv: str = "research_data/AVAX_USDT_ATOM_USDT_kalman.csv",
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Main entry point. Simulate the full AVAX/ATOM pairs strategy.

        Reads the Kalman filter CSV candle by candle, applies entry and
        exit rules, records every trade, and produces the complete
        strategy performance report.

        Parameters
        ----------
        kalman_csv : str
            Path to the Kalman filter output CSV.
            Produced by kalman_filter_engine.py.
            Default path matches the Kalman engine's output path.

        Returns
        -------
        Tuple of three DataFrames:
            trade_log     : every completed trade with full detail
            summary       : aggregate strategy performance statistics
            equity_curve  : cumulative P&L over time
        """

        # claude code changed: new Step 0 block (whole block below) — was previously not resolved at all, pair identity was module-level constant
        # ── Step 0: Resolve pair identity ────────────────────────────────────
        # If the constructor wasn't given pair_name explicitly, parse it from
        # the kalman CSV's filename. Callers that hand this a scratch file
        # that doesn't follow kalman_filter_engine.py's naming convention
        # (a walk-forward fold's "train_slice_kalman.csv", a permutation
        # test's "shuffled_kalman.csv") never set pair_name and are not
        # expecting a hard failure here, so fall back to the AVAX/ATOM
        # module defaults with a warning instead of raising.
        if self.pair_name is None:                                            # claude code changed: new
            try:                                                              # claude code changed: new
                self.pair_name, self.symbol_a, self.symbol_b = (              # claude code changed: new
                    _parse_pair_from_kalman_filename(kalman_csv)              # claude code changed: new
                )                                                              # claude code changed: new
            except ValueError as e:                                           # claude code changed: new
                logger.warning(                                               # claude code changed: new
                    f"Could not resolve pair identity from kalman CSV "       # claude code changed: new
                    f"filename ({e}) Falling back to '{PAIR_NAME}' — pass "   # claude code changed: new
                    f"pair_name/symbol_a/symbol_b to EntryExitEngine "        # claude code changed: new
                    f"explicitly if this is a real pair, not a scratch or "   # claude code changed: new
                    f"shuffled slice."                                        # claude code changed: new
                )                                                              # claude code changed: new
                self.pair_name = PAIR_NAME                                     # claude code changed: new
                self.symbol_a  = SYMBOL_A                                      # claude code changed: new
                self.symbol_b  = SYMBOL_B                                      # claude code changed: new

        logger.info("=" * 70)
        logger.info(f"ENTRY/EXIT ENGINE — {self.pair_name} KALMAN STRATEGY")   # claude code changed: was hardcoded "ENTRY/EXIT ENGINE — AVAX/ATOM KALMAN STRATEGY"
        logger.info("=" * 70)

        # ── Step 1: Load Kalman output ────────────────────────────────────────
        df = self._load_kalman_data(kalman_csv)

        # ── Step 2: Validate required columns ────────────────────────────────
        self._validate_columns(df)

        # ── Step 3: Scan every candle and apply rules ─────────────────────────
        signal_log = self._scan_candles(df)

        # ── Step 4: Build trade log from completed trades ─────────────────────
        trade_log = self._build_trade_log()

        # ── Step 5: Calculate equity curve ────────────────────────────────────
        equity_curve = self._build_equity_curve(trade_log, df)

        # ── Step 6: Calculate strategy summary ───────────────────────────────
        summary = self._build_strategy_summary(trade_log, equity_curve)

        # ── Step 7: Save all outputs ──────────────────────────────────────────
        self._save_outputs(trade_log, summary, equity_curve, signal_log)

        # ── Step 8: Print performance report ─────────────────────────────────
        self._print_performance_report(summary, trade_log)

        return trade_log, summary, equity_curve


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: LOAD KALMAN DATA
    # ─────────────────────────────────────────────────────────────────────────

    def _load_kalman_data(self, kalman_csv: str) -> pd.DataFrame:
        """
        Load the Kalman filter output CSV produced by kalman_filter_engine.py.

        The CSV must contain the columns produced by the Kalman engine.
        If the file does not exist, the error message tells the user
        exactly which module to run first.
        """

        logger.info(f"Step 1: Loading Kalman data from {kalman_csv}...")

        path = Path(kalman_csv)

        if not path.exists():
            raise FileNotFoundError(
                f"Kalman CSV not found at {kalman_csv}. "
                f"Run kalman_filter_engine.py first:\n"
                f"    python -m bot.research.kalman_filter_engine"
            )

        df = pd.read_csv(path)

        # Parse timestamp and set as DatetimeIndex
        # The Kalman engine saves timestamp as a plain column
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.set_index("timestamp", inplace=True)

        df.sort_index(inplace=True)    # Ensure chronological order

        # Remove warmup period — Kalman estimates are unreliable during warmup
        # The 'is_warmup' column is set by kalman_filter_engine.py
        if "is_warmup" in df.columns:
            warmup_count = df["is_warmup"].sum()
            df = df[~df["is_warmup"]].copy()    # Keep only post-warmup candles
            logger.info(
                f"  Removed {warmup_count} warmup candles "
                f"({warmup_count / 24:.0f} days)"
            )

        # ── Guard against corrupted tail/head rows ────────────────────────────
        # The Kalman engine's rolling/lag computations can leave NaN or inf
        # in the last row(s) of the file (e.g. a rolling window that hasn't
        # been recomputed yet, or a lag column with nothing to look back on).
        # If those rows survive into the simulation, they silently poison
        # every downstream statistic that touches them — most visibly the
        # Entry IC calculation, which returns NaN the instant a single NaN
        # reaches scipy.stats.spearmanr (its default nan_policy is
        # 'propagate', not 'omit'). Drop them here, at the source, so every
        # later step in the pipeline is working with clean data.
        required_for_cleanliness = [
            "kalman_zscore", "kalman_zscore_lag1", "pair_signal_dynamic",
            "kalman_beta", "kalman_spread", "prediction_error", "beta_uncertainty",
        ]
        present = [c for c in required_for_cleanliness if c in df.columns]
        before_rows = len(df)
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=present)
        dropped_rows = before_rows - len(df)
        if dropped_rows > 0:
            logger.warning(
                f"  Dropped {dropped_rows} row(s) with NaN/inf in Kalman "
                f"columns before simulation (likely rolling-window edge rows)"
            )

        # claude code changed: new block — candle resampling (CANDLE_RESAMPLE_HOURS
        # module comment / third design iteration). Takes the LAST 1h reading in
        # each N-hour window, so entry/exit decisions only see a fresh z-score
        # every N hours instead of every 1h tick. kalman_zscore_lag1 is recomputed
        # from the resampled series — the original column was a 1h lag, which is
        # meaningless once each row represents N hours. Every other exit rule
        # (hours_held, time-stop) is driven by real pd.Timestamp arithmetic, not
        # row counts, so it needs no changes to work correctly post-resample.
        if self.resample_hours and self.resample_hours > 1:
            before_resample = len(df)
            df = df.resample(f"{self.resample_hours}h").last()
            df = df.dropna(subset=present)
            if "kalman_zscore" in df.columns:
                df["kalman_zscore_lag1"] = df["kalman_zscore"].shift(1).bfill()
            logger.info(
                f"  Resampled to {self.resample_hours}h candles: "
                f"{before_resample:,} -> {len(df):,} candles"
            )

        logger.info(
            f"  Loaded: {len(df):,} candles "
            f"({df.index[0].date()} → {df.index[-1].date()})"
        )

        return df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: VALIDATE COLUMNS
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_columns(self, df: pd.DataFrame) -> None:
        """
        Verify all required Kalman columns are present.

        These columns are produced by kalman_filter_engine.py.
        If any are missing, the Kalman engine may be an older version.
        """

        # Columns this engine absolutely requires from the Kalman output
        required = [
            "kalman_zscore",         # Primary signal — the z-score of the dynamic spread
            "kalman_zscore_lag1",    # Confirmation filter — previous candle's z-score
            "pair_signal_dynamic",   # Composite signal (inverted z-score + momentum)
            "kalman_beta",           # Dynamic hedge ratio — needed for position sizing
            "kalman_spread",         # Raw spread value — needed for P&L calculation
            "prediction_error",      # Kalman health — needed for position sizing
            "beta_uncertainty",      # Kalman confidence — needed for position sizing
        ]

        missing = [c for c in required if c not in df.columns]

        if missing:
            raise ValueError(
                f"Kalman CSV is missing required columns: {missing}. "
                f"Re-run kalman_filter_engine.py to regenerate the CSV. "
                f"Available columns: {list(df.columns)}"
            )

        logger.info(f"  Column validation passed — all {len(required)} required columns present")


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: SCAN CANDLES
    # ─────────────────────────────────────────────────────────────────────────

    def _scan_candles(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Scan every candle sequentially and apply entry/exit rules.

        This is the core simulation loop. It processes candles one at a time
        in chronological order — exactly as a live trading system would.
        No look-ahead: at each candle we only use information available
        at that point in time.

        The loop maintains state between candles:
            - Is there a currently open trade?
            - How long has it been open?
            - What is the current unrealised P&L?
            - Has the partial exit fired?

        For each candle the logic is:
            1. If IN a trade: check exit conditions first
            2. If NOT in a trade: check entry conditions
            3. Never check entry conditions when already in a trade
               (we trade one position at a time in this v1.0 implementation)

        Returns a signal log DataFrame showing strategy state at every candle.
        """

        logger.info(f"Step 3: Scanning {len(df):,} candles...")

        # Pre-allocate signal log arrays
        n               = len(df)
        signal_values   = np.full(n, np.nan)      # pair_signal_dynamic at each candle
        position_status = np.full(n, "", dtype=object)  # "FLAT", "LONG_SPREAD", "SHORT_SPREAD"
        position_size   = np.full(n, 0.0)          # Current position size in USDT
        zscore_values   = np.full(n, np.nan)        # Kalman z-score at each candle
        entry_signal    = np.full(n, False)         # Did we enter this candle?
        exit_signal     = np.full(n, False)         # Did we exit this candle?
        exit_reasons    = np.full(n, "", dtype=object)  # Exit reason if applicable

        timestamps = df.index                       # Candle timestamps
        n_entries  = 0                              # Count entries for logging
        n_exits    = 0                              # Count exits for logging

        # Reset state for clean simulation run
        self.current_trade  = None
        self.trade_id_counter = 0
        self.completed_trades = []
        self.partial_exited = False

        for i, (ts, row) in enumerate(df.iterrows()):

            # ── Extract this candle's Kalman values ───────────────────────────
            zscore       = float(row["kalman_zscore"])             # Current z-score
            zscore_lag   = float(row["kalman_zscore_lag1"])        # Previous z-score
            signal       = float(row["pair_signal_dynamic"])       # Composite signal
            beta         = float(row["kalman_beta"])               # Dynamic hedge ratio
            spread       = float(row["kalman_spread"])             # Raw spread value
            pred_error   = float(row["prediction_error"])          # Filter prediction error
            beta_uncert  = float(row["beta_uncertainty"])          # Filter uncertainty

            # Record signal and z-score for the signal log
            signal_values[i] = signal
            zscore_values[i] = zscore

            # ── If currently IN a trade: check exit conditions ─────────────────
            if self.current_trade is not None:

                position_status[i] = self.current_trade.direction
                position_size[i]   = self.current_trade.position_usdt

                # Update max adverse and favorable moves for trade quality tracking
                if self.current_trade.direction == "LONG_SPREAD":
                    # We are long spread: positive zscore moves against us
                    if zscore > self.current_trade.max_adverse_zscore:
                        self.current_trade.max_adverse_zscore = zscore
                    if zscore < self.current_trade.max_favorable_zscore:
                        self.current_trade.max_favorable_zscore = zscore
                else:
                    # We are short spread: negative zscore moves against us
                    if zscore < self.current_trade.max_adverse_zscore:
                        self.current_trade.max_adverse_zscore = zscore
                    if zscore > self.current_trade.max_favorable_zscore:
                        self.current_trade.max_favorable_zscore = zscore

                # ── Check partial exit ────────────────────────────────────────
                # When |z| drops below EXIT_PARTIAL_ZSCORE for the first time
                # close PARTIAL_FRACTION of the position to lock in some profit
                if not self.partial_exited:
                    if abs(zscore) < self.exit_partial_z:
                        self._execute_partial_exit(ts, zscore, spread, i)
                        exit_signal[i]  = True
                        exit_reasons[i] = "PARTIAL"
                        self.partial_exited = True
                        n_exits += 1

                # ── Check full exit conditions ────────────────────────────────
                exit_reason = self._check_exit_conditions(
                    ts, zscore, spread, i
                )

                if exit_reason:
                    # Execute the exit — close the remaining position
                    self._execute_full_exit(ts, zscore, spread, exit_reason)
                    exit_signal[i]  = True
                    # NOTE: a partial exit and the full exit can legitimately
                    # fire on the SAME candle (the z-score can cross both the
                    # partial band and the target band within one hourly bar).
                    # Overwriting exit_reasons[i] here used to erase the fact
                    # that a partial exit happened first — the signal log then
                    # claimed 0 partial exits even when they occurred. Record
                    # both instead of clobbering.
                    if exit_reasons[i]:
                        exit_reasons[i] = f"{exit_reasons[i]}+{exit_reason}"
                    else:
                        exit_reasons[i] = exit_reason
                    position_status[i] = "FLAT"    # Now flat
                    position_size[i]   = 0.0
                    n_exits += 1

            # ── If NOT in a trade: check entry conditions ──────────────────────
            else:
                position_status[i] = "FLAT"        # Currently no position
                position_size[i]   = 0.0

                # Check if this candle generates an entry signal
                if self._check_entry_conditions(
                    zscore, zscore_lag, signal,
                    beta_uncert, pred_error
                ):
                    # Determine direction from z-score sign
                    # Positive z: AVAX expensive → SHORT_SPREAD (short AVAX, long ATOM)
                    # Negative z: AVAX cheap    → LONG_SPREAD  (long AVAX, short ATOM)
                    direction = "SHORT_SPREAD" if zscore > 0 else "LONG_SPREAD"

                    # Size the position using Kelly criterion
                    total, leg_a, leg_b, strength = self.sizer.size_position(
                        zscore=zscore,
                        beta=beta,
                        beta_uncertainty=beta_uncert,
                        prediction_error=pred_error,
                    )

                    # Skip if position size is below minimum
                    if total < MIN_POSITION_USDT:
                        continue

                    # claude code changed: new — per-trade stop level, replacing the fixed
                    # EXIT_ZSCORE_STOPLOSS every trade used to be checked against
                    # regardless of how extreme its own entry was. Capped at
                    # ZSCORE_WINSOR_LIMIT since kalman_zscore can never report beyond it.
                    stop_zscore = min(
                        abs(zscore) + self.exit_stop_distance,
                        ZSCORE_WINSOR_LIMIT,
                    )

                    # claude code changed: new — per-trade, entry-depth-scaled
                    # profit target. Signed so it sits on the OPPOSITE side of
                    # zero from the entry (e.g. entry z=+3.0 -> target=-0.3):
                    # the exit condition checks for an actual crossing through
                    # equilibrium, not just approaching it. See
                    # EXIT_TARGET_OVERSHOOT_FRACTION's module comment for the
                    # full rationale.
                    target_zscore = (
                        -np.sign(zscore) * self.exit_target_overshoot * abs(zscore)
                    )

                    # claude code changed: new — per-trade, entry-depth-scaled
                    # minimum hold before TARGET can fire. See
                    # EXIT_MIN_HOLD_BASE_HOURS's module comment: a threshold-
                    # level entry (|z|=entry_threshold) gets the base value;
                    # deeper entries get proportionally longer, since a bigger
                    # claimed edge should take proportionally longer to
                    # genuinely resolve, not just travel further.
                    min_hold_hours = (
                        self.exit_min_hold_base_hours * abs(zscore) / self.entry_threshold
                    )

                    # Open new trade record
                    self.trade_id_counter += 1
                    self.current_trade = TradeRecord(
                        trade_id                  = self.trade_id_counter,
                        entry_timestamp           = ts,
                        entry_zscore              = zscore,
                        entry_signal              = signal,
                        entry_beta                = beta,
                        entry_spread               = spread,
                        direction                 = direction,
                        position_usdt             = total,
                        leg_a_usdt                = leg_a,
                        leg_b_usdt                = leg_b,
                        kelly_fraction            = self.sizer.safe_kelly,
                        signal_strength           = strength,
                        stop_zscore               = stop_zscore,   # claude code changed: new
                        target_zscore             = target_zscore,   # claude code changed: new
                        min_hold_hours            = min_hold_hours,   # claude code changed: new
                        max_adverse_zscore        = zscore,    # Initialise to entry
                        max_favorable_zscore      = zscore,    # Initialise to entry
                        prediction_error_at_entry = pred_error,
                        beta_uncertainty_at_entry = beta_uncert,
                    )

                    self.partial_exited = False    # Reset partial exit flag

                    position_status[i] = direction
                    position_size[i]   = total
                    entry_signal[i]    = True
                    n_entries += 1

        logger.info(
            f"  Scan complete: {n_entries} entries, {n_exits} exits, "
            f"{len(self.completed_trades)} completed trades"
        )

        # ── Close any trade still open at the end of data ─────────────────────
        # This prevents open trades from being ignored in the analysis
        if self.current_trade is not None:
            last_ts     = df.index[-1]
            last_zscore = float(df["kalman_zscore"].iloc[-1])
            last_spread = float(df["kalman_spread"].iloc[-1])
            self._execute_full_exit(
                last_ts, last_zscore, last_spread, "DATA_END"
            )
            logger.info("  Closed 1 trade that was still open at end of data")

        # Build signal log DataFrame
        signal_log_df = pd.DataFrame({
            "timestamp":      timestamps,
            "kalman_zscore":  zscore_values,
            "signal":         signal_values,
            "position_status": position_status,
            "position_usdt":  position_size,
            "entry_signal":   entry_signal,
            "exit_signal":    exit_signal,
            "exit_reason":    exit_reasons,
        }).set_index("timestamp")

        return signal_log_df


    # ─────────────────────────────────────────────────────────────────────────
    # ENTRY CONDITIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _check_entry_conditions(
        self,
        zscore:       float,
        zscore_lag:   float,
        signal:       float,
        beta_uncert:  float,
        pred_error:   float,
    ) -> bool:
        """
        Check whether the current candle meets all conditions to open a trade.

        All conditions must be TRUE simultaneously to enter.
        This belt-and-suspenders approach reduces false entries.

        Condition 1 — Z-score threshold:
            |kalman_zscore| must exceed ENTRY_ZSCORE_THRESHOLD (2.0)
            Derived from: 5.7% of candles have |z| > 2.0
            Below 2.0: too many signals, edge is diluted by noise

        Condition 2 — Lag confirmation:
            kalman_zscore_lag1 must be in the same direction as current z
            Derived from: lag1 has IC = 0.3905 (STRONG KEEP)
            This filters single-candle spikes from manipulation events
            A genuine divergence persists for at least 2 consecutive candles

        Condition 3 — Composite signal agreement:
            pair_signal_dynamic must agree with z-score direction
            Both are derived from the same z-score but with different weighting
            Agreement means the signal is internally consistent

        Condition 4 — Beta uncertainty filter:
            beta_uncertainty must be below MAX_BETA_UNCERTAINTY (0.10)
            When the Kalman is uncertain about β, the spread is less reliable
            This is a cointegration health check built into every entry

        Condition 5 — Prediction error filter:
            Prediction error must not be extreme (3× normal)
            Extreme filter surprise = possible regime change in progress
            Wait for filter to stabilise before entering new positions

        Returns True if all conditions pass, False otherwise.
        """

        # Condition 1: Z-score must be extreme enough to trade
        # This is the primary signal condition — everything else confirms it
        if abs(zscore) < self.entry_threshold:
            return False    # Z-score not extreme enough — no edge above noise

        # Condition 2: Lag confirmation — previous candle must agree
        # Both current and previous z-score must be on the same side of zero
        # This prevents entering on a single-candle spike that immediately reverses
        if self.require_confirm:
            if zscore > 0 and zscore_lag <= 0:
                return False    # z is positive but was negative last candle — spike
            if zscore < 0 and zscore_lag >= 0:
                return False    # z is negative but was positive last candle — spike

        # Condition 3: Composite signal must agree with z-score direction
        # pair_signal_dynamic = -z-score by construction, so:
        #   If z > 0 (short spread signal): pair_signal_dynamic should be negative
        #   If z < 0 (long spread signal): pair_signal_dynamic should be positive
        if zscore > 0 and signal > -ENTRY_SIGNAL_MIN:
            return False    # Z says short but composite signal does not confirm
        if zscore < 0 and signal < ENTRY_SIGNAL_MIN:
            return False    # Z says long but composite signal does not confirm

        # Condition 4: Beta uncertainty must be acceptable
        # High uncertainty = Kalman is not sure about the hedge ratio
        # In this state, the spread calculation is less reliable
        if beta_uncert > MAX_BETA_UNCERTAINTY:
            return False    # Too uncertain about hedge ratio — skip this candle

        # Condition 5: Prediction error must not be extreme
        # 3× normal std = the filter is very surprised by recent prices
        # This often precedes a regime change or structural break
        if abs(pred_error) > PRED_ERROR_NORMAL * 3:
            return False    # Filter is in a surprised state — wait for stability

        # All conditions passed — this is a valid entry signal
        return True


    # ─────────────────────────────────────────────────────────────────────────
    # EXIT CONDITIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _check_exit_conditions(
        self,
        ts:     pd.Timestamp,
        zscore: float,
        spread: float,
        idx:    int,
    ) -> Optional[str]:
        """
        Check whether the current candle triggers an exit from the open trade.

        Three independent exit conditions — any one fires the exit.
        The exit reason is recorded in the trade log for analysis.

        Exit 1 — TARGET (profit exit):
            claude code changed: was `abs(zscore) <= EXIT_ZSCORE_TARGET` (0.3)
            for every trade — see EXIT_TARGET_OVERSHOOT_FRACTION's module
            comment for why that made TARGET a near-universal, uninformative
            outcome (research_data/model_governance_log.md). Now checks
            whether the spread has crossed THROUGH equilibrium to THIS
            TRADE'S OWN target_zscore, on the opposite side from entry,
            computed once at entry as -sign(entry_z) * overshoot * |entry_z|
            — same per-trade pattern stop_zscore already uses. claude code
            changed: additionally gated on hours_held >= THIS TRADE'S OWN
            min_hold_hours, computed once at entry as EXIT_MIN_HOLD_BASE_HOURS
            * |entry_z| / entry_threshold — see that constant's module
            comment for why the bar now scales with entry depth instead of a
            flat 24h for every trade. This gate applies to TARGET only —
            Exit 2/3 below are never delayed by it. This is the desired
            outcome — capture a confirmed reversion move

        Exit 2 — STOPLOSS:
            |z-score| has exceeded THIS TRADE'S OWN stop_zscore, computed at
            entry as |entry_zscore| + EXIT_ZSCORE_STOP_DISTANCE (capped at
            ZSCORE_WINSOR_LIMIT). claude code changed: was a check against the
            same fixed EXIT_ZSCORE_STOPLOSS (4.5) for every trade regardless of
            entry point — see the EXIT_ZSCORE_STOPLOSS module constant comment
            for why that was a structural bias, not just a AVAX/ATOM quirk.
            The spread has moved further against us — not reverting
            This means either: (a) manipulation event, (b) cointegration break
            Cut the loss immediately — do not wait for mean reversion

        Exit 3 — TIMESTOP:
            Position has been held for EXIT_TIME_STOP_HOURS (240h) or longer
            Derived from: 2 × half-life (119.9h × 2 = 239.8h)
            If spread has not reverted in 10 days, something structural changed
            The expected reversion time has been exceeded — exit regardless of P&L

        Returns the exit reason string if exit triggered, None if no exit.
        """

        trade = self.current_trade    # Reference to the open trade

        # Calculate hours this trade has been open
        # Using pd.Timestamp arithmetic — handles timezone correctly
        hours_held = (ts - trade.entry_timestamp).total_seconds() / 3600.0

        # ── Exit 1: Target hit — z-score crossed through equilibrium ─────────
        # claude code changed: was `abs(zscore) <= self.exit_target` — see
        # docstring above and EXIT_TARGET_OVERSHOOT_FRACTION's module comment.
        # trade.target_zscore is signed opposite to the entry direction, so
        # "crossed the target" means zscore has moved PAST it, not just
        # approached zero from the entry side.
        # For LONG_SPREAD: entered when z was very negative; target_zscore is
        # positive — exit once z has risen AT LEAST that far past zero.
        # For SHORT_SPREAD: entered when z was very positive; target_zscore is
        # negative — exit once z has fallen AT LEAST that far past zero.
        #
        # claude code changed: new — gated on hours_held >= exit_min_hold_hours.
        # See EXIT_MIN_HOLD_HOURS's module comment for the full rationale:
        # median hold time (2-9h) is far below the half-life (120h), so most
        # TARGET exits were near-instant whipsaws rather than resolved
        # reversion. This gate does NOT apply to STOPLOSS/TIMESTOP below —
        # real risk is never suppressed while a trade waits out this window,
        # only profit-taking is delayed.
        # claude code changed: was `self.exit_min_hold_hours` (flat 24h for
        # every trade) — now checks THIS trade's own min_hold_hours, scaled
        # to its entry depth (see EXIT_MIN_HOLD_BASE_HOURS module comment).
        if hours_held >= trade.min_hold_hours:
            if trade.direction == "LONG_SPREAD" and zscore >= trade.target_zscore:
                return "TARGET"    # Spread has crossed through equilibrium — take profit
            if trade.direction == "SHORT_SPREAD" and zscore <= trade.target_zscore:
                return "TARGET"    # Spread has crossed through equilibrium — take profit

        # ── Exit 2: Stop loss — z-score moved further against us ─────────────
        # claude code changed: checks trade.stop_zscore (this trade's own, entry-
        # relative stop level) instead of the fixed self.exit_stoploss every
        # trade used to share regardless of how extreme its entry was.
        if trade.direction == "LONG_SPREAD" and zscore < -trade.stop_zscore:
            return "STOPLOSS"    # Spread moved further against our long position
        if trade.direction == "SHORT_SPREAD" and zscore > trade.stop_zscore:
            return "STOPLOSS"    # Spread moved further against our short position

        # ── Exit 3: Time stop — held too long ────────────────────────────────
        # 240 hours = 10 days = 2 × half-life of 119.9 hours
        # If the spread has not reverted in twice the expected time,
        # the cointegration relationship may be temporarily broken
        if hours_held >= self.exit_time_stop:
            return "TIMESTOP"    # Time limit exceeded — exit to free capital

        # No exit condition triggered — hold the position
        return None


    # ─────────────────────────────────────────────────────────────────────────
    # EXECUTE PARTIAL EXIT
    # ─────────────────────────────────────────────────────────────────────────

    def _execute_partial_exit(
        self,
        ts:     pd.Timestamp,
        zscore: float,
        spread: float,
        idx:    int,
    ) -> None:
        """
        Close a fraction of the position when z-score first crosses EXIT_PARTIAL_ZSCORE.

        Why partial exit?
            The full target is |z| < 0.3 (near zero mean reversion).
            But often the z-score oscillates around the target before
            fully reverting. A partial exit at |z| < 1.0 locks in
            partial profit while keeping half the position running
            toward the full target.

            This is standard risk management for pairs trading:
            "Take some off the table when you are up, let the rest run."

        The partial exit closes EXIT_PARTIAL_FRACTION (50%) of the position.
        The remaining 50% continues to be managed by the full exit rules.

        P&L for the partial exit:
            spread_move = entry_spread - current_spread  (for SHORT_SPREAD)
            spread_move = current_spread - entry_spread  (for LONG_SPREAD)
            gross_pnl_pct = expm1(spread_move)   — see _execute_full_exit()'s
                docstring for the full rationale (same fix, same reasoning).
            net_pnl_pct = gross_pnl_pct - transaction_cost_on_partial
        """

        trade = self.current_trade

        # Calculate spread move in the correct direction
        if trade.direction == "LONG_SPREAD":
            # We are long spread — profit when spread increases
            spread_move = spread - trade.entry_spread
        else:
            # We are short spread — profit when spread decreases
            spread_move = trade.entry_spread - spread

        # claude code changed: was `spread_move / abs(trade.entry_spread)` — see
        # _execute_full_exit()'s docstring/comment for the full rationale (same
        # bug, same fix, fixed in both places since they duplicated the formula).
        gross_pnl = np.expm1(spread_move)

        # Transaction cost on the partial close (50% of position × round-trip cost)
        partial_cost  = self.total_transaction_cost * self.exit_partial_frac   # claude code changed: was TOTAL_TRANSACTION_COST module constant
        net_pnl       = gross_pnl - partial_cost
        partial_usdt  = trade.position_usdt * self.exit_partial_frac

        # ── Record the realised partial P&L on the trade itself ───────────────
        # This used to be computed and only logged at DEBUG level, then
        # discarded — the closed fraction's profit never made it into the
        # trade's net_pnl_usdt, the equity curve, or the summary stats.
        # Store it here so _execute_full_exit can fold it into the trade's
        # final realised P&L.
        trade.partial_pnl_usdt   += net_pnl * partial_usdt
        trade.partial_exit_zscore = zscore

        # Reduce position size by the partial fraction
        # The remaining position continues with existing trade record
        trade.position_usdt *= (1.0 - self.exit_partial_frac)
        trade.leg_a_usdt    *= (1.0 - self.exit_partial_frac)
        trade.leg_b_usdt    *= (1.0 - self.exit_partial_frac)

        logger.debug(
            f"    PARTIAL EXIT: trade {trade.trade_id} | "
            f"z={zscore:.2f} | "
            f"gross={gross_pnl:.4f} | net={net_pnl:.4f} | "
            f"closed ${partial_usdt:.0f}"
        )


    # ─────────────────────────────────────────────────────────────────────────
    # EXECUTE FULL EXIT
    # ─────────────────────────────────────────────────────────────────────────

    def _execute_full_exit(
        self,
        ts:          pd.Timestamp,
        zscore:      float,
        spread:      float,
        exit_reason: str,
    ) -> None:
        """
        Close the remaining position and record the completed trade.

        Calculates the final P&L for the trade and stores the complete
        trade record in self.completed_trades for analysis.

        P&L calculation:
            The spread is measured in log-price units:
                spread_t = log(AVAX_t) - α_t - β_t × log(ATOM_t)
            which is already a log-return-equivalent linear combination of the
            two legs' prices — a raw CHANGE in spread of 0.02 corresponds
            directly to a ~2% return on the (dollar-neutral) combined
            position, the same way a change in log-price corresponds to a
            simple-return via expm1(). No further normalisation is needed —
            or economically justified.

            claude code changed: was `spread_move / abs(entry_spread)` —
            entry_spread is itself a mean-reverting quantity that sits near
            zero by construction (the strategy only enters when it's several
            std-devs from ITS OWN rolling mean, but the raw level is
            unrelated to trade risk or size), so using it as a percentage-of-
            price denominator was a category error — like computing a
            stock's return as (price_change / distance-from-its-own-moving-
            average) instead of (price_change / price). Empirically this
            produced 83-97% average "returns" per trade for a market-neutral
            pairs strategy, which is not economically plausible. Confirmed
            by checking raw spread_move directly: it already averages ~2.7%
            in magnitude — exactly the range expected for this kind of
            trade — so it needed no division at all, just the log-return ->
            simple-return conversion (expm1), included for precision though
            negligible at these magnitudes. See
            research_data/model_governance_log.md's "P&L normalisation bug"
            entry for the full investigation, including why this was missed
            by every downstream check (win_rate/Sharpe/entry_ic) despite
            being wrong — they're all internally consistent under either
            formula, so nothing failed loudly; only comparing the resulting
            P&L magnitude against economic plausibility caught it.

            For SHORT_SPREAD (entered when spread was too high):
                We profit when spread falls back to equilibrium
                gross_pnl_pct = expm1(entry_spread - exit_spread)

            For LONG_SPREAD (entered when spread was too low):
                We profit when spread rises back to equilibrium
                gross_pnl_pct = expm1(exit_spread - entry_spread)

            Transaction costs (fee + slippage, both legs, both entry and exit):
                total_cost = TOTAL_TRANSACTION_COST = 0.3% round trip

            Net P&L in USDT:
                net_pnl_usdt = trade.position_usdt × net_pnl_pct
        """

        trade = self.current_trade    # The trade we are closing

        # Hours held — from entry timestamp to this exit timestamp
        hours_held = (ts - trade.entry_timestamp).total_seconds() / 3600.0

        # ── Calculate gross P&L ───────────────────────────────────────────────
        if trade.direction == "LONG_SPREAD":
            # Long spread: we profit when spread rises
            spread_move = spread - trade.entry_spread
        else:
            # Short spread: we profit when spread falls
            spread_move = trade.entry_spread - spread

        # claude code changed: was `spread_move / abs(trade.entry_spread)` —
        # see the docstring above for the full rationale. expm1 converts the
        # log-return (spread_move) to a simple return; identical to
        # spread_move itself to ~4 decimal places at these magnitudes, kept
        # for correctness rather than because it changes the numbers here.
        gross_pnl_pct = np.expm1(spread_move)

        # ── Apply transaction costs ───────────────────────────────────────────
        # Full round-trip cost: fee + slippage on both legs, entry + exit
        fee_cost_pct  = self.total_transaction_cost   # claude code changed: was TOTAL_TRANSACTION_COST module constant
        net_pnl_pct   = gross_pnl_pct - fee_cost_pct

        # ── Convert to USDT ───────────────────────────────────────────────────
        # trade.position_usdt at this point already reflects any partial exit
        # (it was reduced in _execute_partial_exit), so this correctly prices
        # only the REMAINING position. The partial exit's own realised P&L
        # (trade.partial_pnl_usdt) is added back in below — previously it was
        # dropped entirely, silently understating total trade P&L whenever a
        # partial exit had fired.
        remaining_pnl_usdt = trade.position_usdt * net_pnl_pct
        net_pnl_usdt        = remaining_pnl_usdt + trade.partial_pnl_usdt

        # ── Update trade record with exit information ─────────────────────────
        trade.exit_timestamp  = ts
        trade.exit_zscore     = zscore
        trade.exit_reason     = exit_reason
        trade.hours_held      = hours_held
        trade.gross_pnl_pct   = gross_pnl_pct
        trade.fee_cost_pct    = fee_cost_pct
        trade.net_pnl_pct     = net_pnl_pct
        trade.net_pnl_usdt    = net_pnl_usdt

        # Store completed trade
        self.completed_trades.append(trade)

        logger.debug(
            f"    EXIT [{exit_reason}]: trade {trade.trade_id} | "
            f"entry z={trade.entry_zscore:.2f} exit z={zscore:.2f} | "
            f"held {hours_held:.0f}h | "
            f"net P&L: {net_pnl_pct:.4f} (${net_pnl_usdt:.1f})"
        )

        # Clear the current trade — we are now flat
        self.current_trade = None


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: BUILD TRADE LOG
    # ─────────────────────────────────────────────────────────────────────────

    def _build_trade_log(self) -> pd.DataFrame:
        """
        Convert completed trade records to a DataFrame for analysis and saving.

        Each row is one completed trade with all entry, exit, sizing,
        and P&L details. This is the primary output for:
            - Walk-forward validation (Missing Piece 5)
            - Strategy performance analysis
            - Teaching material (concrete examples of the strategy in action)
        """

        if not self.completed_trades:
            logger.warning("No completed trades to build log from.")
            return pd.DataFrame()

        # Convert each TradeRecord to a dictionary and build DataFrame
        rows = [trade.to_dict() for trade in self.completed_trades]
        trade_log = pd.DataFrame(rows)

        # Sort chronologically by entry time
        trade_log = trade_log.sort_values("entry_timestamp").reset_index(drop=True)

        logger.info(f"Step 4: Trade log built — {len(trade_log)} trades")

        return trade_log


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: BUILD EQUITY CURVE
    # ─────────────────────────────────────────────────────────────────────────

    def _build_equity_curve(
        self,
        trade_log:  pd.DataFrame,
        df:         pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Build the cumulative P&L curve over time.

        The equity curve shows how the strategy's total capital grew (or shrank)
        over the simulation period. It is the most important single visualisation
        for assessing strategy quality:

            Steady upward slope → consistent edge, low regime dependence
            Volatile with drawdowns → regime-sensitive or over-fitted
            Flat periods → signal not firing (low regime)
            Sharp drops → stop loss events (manipulation or regime breaks)

        The equity curve is built on the trade log — each trade's net P&L
        is added sequentially to the running capital total.
        """

        if trade_log.empty:
            return pd.DataFrame()

        # Start with the initial capital
        cumulative_pnl_usdt = STRATEGY_CAPITAL_USDT

        equity_rows = []

        for _, trade in trade_log.iterrows():
            cumulative_pnl_usdt += trade["net_pnl_usdt"]   # Add this trade's P&L
            equity_rows.append({
                "timestamp":         trade["exit_timestamp"],    # When the trade closed
                "trade_id":          trade["trade_id"],           # Which trade
                "net_pnl_usdt":      trade["net_pnl_usdt"],       # This trade's P&L
                "cumulative_usdt":   cumulative_pnl_usdt,         # Running total
                "cumulative_return": (                            # % return from start
                    cumulative_pnl_usdt / STRATEGY_CAPITAL_USDT - 1.0
                ),
                "exit_reason":       trade["exit_reason"],        # How this trade ended
            })

        equity_df = pd.DataFrame(equity_rows)

        # Calculate drawdown — how far below the peak is the current equity?
        # Maximum drawdown is the key risk metric for a trading strategy
        equity_df["rolling_peak"] = equity_df["cumulative_usdt"].cummax()
        equity_df["drawdown_pct"] = (
            (equity_df["cumulative_usdt"] - equity_df["rolling_peak"]) /
            equity_df["rolling_peak"]
        )

        return equity_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: BUILD STRATEGY SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    def _build_strategy_summary(
        self,
        trade_log:    pd.DataFrame,
        equity_curve: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Calculate aggregate strategy performance statistics.

        These are the numbers that matter for evaluating whether the strategy
        is ready for walk-forward validation and eventual live deployment.

        Key metrics and their thresholds for this strategy:
            Win rate:     target > 60% (our research showed 68-71%)
            Sharpe ratio: target > 1.0 (institutional minimum)
            Max drawdown: target < 20% (pairs strategies should be low drawdown)
            Profit factor: target > 1.5 (gross profit / gross loss)
            Avg hold time: should be near the half-life (119.9h)
        """

        if trade_log.empty:
            return pd.DataFrame()

        # ── Basic trade counts ────────────────────────────────────────────────
        n_total   = len(trade_log)                        # Total trades
        n_winners = (trade_log["net_pnl_pct"] > 0).sum() # Profitable trades
        n_losers  = (trade_log["net_pnl_pct"] <= 0).sum() # Losing trades
        win_rate  = n_winners / n_total if n_total > 0 else 0.0

        # ── P&L statistics ────────────────────────────────────────────────────
        total_pnl_pct    = trade_log["net_pnl_pct"].sum()
        avg_pnl_pct      = trade_log["net_pnl_pct"].mean()
        avg_winner_pct   = trade_log[trade_log["net_pnl_pct"] > 0]["net_pnl_pct"].mean()
        avg_loser_pct    = trade_log[trade_log["net_pnl_pct"] <= 0]["net_pnl_pct"].mean()
        total_pnl_usdt   = trade_log["net_pnl_usdt"].sum()

        # Profit factor: gross profit / gross loss
        # Above 1.0 = strategy makes more than it loses
        gross_profit = trade_log[trade_log["net_pnl_pct"] > 0]["net_pnl_pct"].sum()
        gross_loss   = abs(trade_log[trade_log["net_pnl_pct"] <= 0]["net_pnl_pct"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf

        # ── Sharpe ratio ──────────────────────────────────────────────────────
        # Annualised Sharpe on per-trade returns
        # For pairs trading: each trade is independent, so we use trade returns
        # Annualisation: assume ~10 trades per week = 520 per year
        pnl_std   = trade_log["net_pnl_pct"].std()
        # claude code changed: was `.days` (integer truncation) — for any
        # trade set spanning under 24h between the first entry and the last
        # exit (this module's own governance log documents 2-9h median
        # holds as common), `.days` truncates to 0 and this line raised
        # ZeroDivisionError, crashing strategy-summary generation entirely.
        # Found while adding entry_exit_engine.py's first test coverage
        # (Phase 1C, Blocker A/Step 4). `.total_seconds() / 86400.0` gives
        # the real fractional-day span instead — for any existing multi-day
        # backtest span (e.g. the AVAX/ATOM 5-year reference run) this only
        # adds back the fractional-day remainder `.days` was discarding, a
        # negligible change to trades_per_year/Sharpe; for a short span it
        # is the difference between a real number and a crash.
        trade_span_days = (
            pd.to_datetime(trade_log["exit_timestamp"].max()) -
            pd.to_datetime(trade_log["entry_timestamp"].min())
        ).total_seconds() / 86400.0
        trades_per_year = (
            n_total / (trade_span_days / 365.25)
            if n_total > 0 and trade_span_days > 0 else 1
        )
        sharpe = (
            (avg_pnl_pct / pnl_std) * np.sqrt(trades_per_year)
            if pnl_std > 0 else 0.0
        )

        # ── Drawdown ──────────────────────────────────────────────────────────
        max_drawdown_pct = equity_curve["drawdown_pct"].min() if not equity_curve.empty else 0.0

        # ── Trade duration ────────────────────────────────────────────────────
        avg_hold_hours = trade_log["hours_held"].mean()
        median_hold_hours = trade_log["hours_held"].median()

        # ── Exit reason breakdown ─────────────────────────────────────────────
        # Exit reasons can now be compound (e.g. "PARTIAL+TARGET") since a
        # partial exit and the full exit are allowed to land on the same
        # candle without one overwriting the other's record. Count each
        # component so PARTIAL exits are no longer invisible in the summary.
        exit_component_counts: Dict[str, int] = {}
        for reason in trade_log["exit_reason"]:
            for component in str(reason).split("+"):
                exit_component_counts[component] = exit_component_counts.get(component, 0) + 1

        # ── IC validation on trade P&L ────────────────────────────────────────
        # Compute IC between entry z-score and trade net P&L
        # High IC = larger entry z-scores produce larger profits (correct)
        # This validates that our entry threshold is correctly calibrated
        #
        # scipy.stats.spearmanr defaults to nan_policy='propagate': a single
        # NaN/inf anywhere in either input silently makes the WHOLE
        # correlation NaN, with no error raised. That is what was producing
        # "Entry IC: nan" even with hundreds of trades. Sanitise the inputs
        # explicitly and set nan_policy='omit' as a second line of defence.
        ic_frame = trade_log[["entry_zscore", "net_pnl_pct"]].replace(
            [np.inf, -np.inf], np.nan
        ).dropna()

        if len(ic_frame) > 10:
            ic_val, ic_p = stats.spearmanr(
                ic_frame["entry_zscore"].abs(),
                ic_frame["net_pnl_pct"],
                nan_policy="omit",
            )
            n_dropped_for_ic = len(trade_log) - len(ic_frame)
            if n_dropped_for_ic > 0:
                logger.warning(
                    f"  Entry IC: excluded {n_dropped_for_ic} trade(s) with "
                    f"NaN/inf entry_zscore or net_pnl_pct"
                )
        else:
            ic_val, ic_p = (np.nan, np.nan)

        # ── Sanity check: hold time vs. half-life ─────────────────────────────
        # The strategy's own research established a ~120h half-life for this
        # spread. If trades are, on average, exiting in a small fraction of
        # that time, it is a strong signal that something upstream is off —
        # either the z-score is noisier at this candle resolution than the
        # half-life estimate assumes, or there is a data issue — rather than
        # confirmation the strategy is unusually efficient. Surface it instead
        # of letting it pass silently under an all-green verdict.
        if (                                                                     # claude code changed: was "if avg_hold_hours > 0 and avg_hold_hours < VALIDATED_HALF_LIFE * 0.25:"
            self.validated_half_life is not None                                # claude code changed: new guard — validated_half_life can now be None
            and avg_hold_hours > 0
            and avg_hold_hours < self.validated_half_life * 0.25                # claude code changed: was VALIDATED_HALF_LIFE
        ):
            logger.warning(
                f"  Avg hold time ({avg_hold_hours:.1f}h) is far below the "
                f"validated half-life ({self.validated_half_life:.0f}h) — "     # claude code changed: was VALIDATED_HALF_LIFE
                f"verify this isn't a symptom of noisy/leaked z-scores "
                f"before trusting the win rate and Sharpe below"
            )

        # ── Build summary dictionary ──────────────────────────────────────────
        summary_dict = {
            "pair":                self.pair_name,                              # claude code changed: was PAIR_NAME
            "n_total_trades":      n_total,
            "n_winners":           n_winners,
            "n_losers":            n_losers,
            "win_rate":            round(win_rate, 4),
            "total_pnl_pct":       round(total_pnl_pct, 4),
            "avg_pnl_pct":         round(avg_pnl_pct, 4),
            "avg_winner_pct":      round(avg_winner_pct, 4) if not np.isnan(avg_winner_pct) else np.nan,
            "avg_loser_pct":       round(avg_loser_pct, 4) if not np.isnan(avg_loser_pct) else np.nan,
            "profit_factor":       round(profit_factor, 4) if not np.isinf(profit_factor) else 999.0,
            "sharpe_ratio":        round(sharpe, 4),
            "max_drawdown_pct":    round(max_drawdown_pct, 4),
            "total_pnl_usdt":      round(total_pnl_usdt, 2),
            "avg_hold_hours":      round(avg_hold_hours, 1),
            "median_hold_hours":   round(median_hold_hours, 1),
            "half_life_hours":     self.validated_half_life if self.validated_half_life is not None else np.nan,   # claude code changed: was VALIDATED_HALF_LIFE
            "exit_target_count":   exit_component_counts.get("TARGET",   0),
            "exit_stoploss_count": exit_component_counts.get("STOPLOSS", 0),
            "exit_timestop_count": exit_component_counts.get("TIMESTOP", 0),
            "exit_partial_count":  exit_component_counts.get("PARTIAL",  0),
            "entry_ic":            round(float(ic_val), 4) if not np.isnan(ic_val) else np.nan,
            "entry_ic_pvalue":     round(float(ic_p), 4) if not np.isnan(ic_p) else np.nan,
            "validated_ic":        self.validated_ic if self.validated_ic is not None else np.nan,             # claude code changed: was VALIDATED_IC
            "validated_win_rate":  self.validated_win_rate if self.validated_win_rate is not None else np.nan, # claude code changed: was VALIDATED_WIN_RATE
            "kelly_safety":        KELLY_SAFETY_FRACTION,
            "asset_class":         self.asset_class,        # claude code changed: new — Phase 1C, Blocker A/Step 3
            "venue_id":            self.venue_id,            # claude code changed: new
            "fee_rate":            self.fee_rate,            # claude code changed: new
            "slippage_rate":       self.slippage_rate,       # claude code changed: new
            "total_transaction_cost": round(self.total_transaction_cost, 6),   # claude code changed: new
            "entry_threshold":     self.entry_threshold,
            "exit_target":         self.exit_target,
            "exit_stoploss":       self.exit_stoploss,        # claude code changed: no longer used to trigger exits — see module constant comment
            "exit_stop_distance":  self.exit_stop_distance,   # claude code changed: new — what actually triggers a stop, per-trade
            "exit_time_stop_h":    self.exit_time_stop,
        }

        return pd.DataFrame([summary_dict])


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: SAVE OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_outputs(
        self,
        trade_log:    pd.DataFrame,
        summary:      pd.DataFrame,
        equity_curve: pd.DataFrame,
        signal_log:   pd.DataFrame,
    ) -> None:
        """
        Save all strategy simulation outputs to research_data directory.

        Filenames are built from the resolved pair (self.symbol_a /
        self.symbol_b, e.g. "AVAX_ATOM" for AVAX_USDT/ATOM_USDT) rather than
        hardcoded, so two different pairs run into the same output_dir don't
        overwrite each other. Four files are saved:

        1. <PAIR>_trade_log.csv — PRIMARY OUTPUT for walk-forward engine
           Every trade with full detail. Feed this directly into
           walk_forward_engine.py (Missing Piece 5).

        2. <PAIR>_strategy_summary.csv — PERFORMANCE REPORT
           Aggregate statistics. These numbers answer:
           "Is this strategy ready for live deployment?"

        3. <PAIR>_equity_curve.csv — VISUAL INSPECTION
           Cumulative P&L curve. Open in Excel or plot in matplotlib.
           Steady upward slope = regime-agnostic edge confirmed.

        4. <PAIR>_signal_log.csv — DEBUGGING AND ANALYSIS
           Every candle's strategy state. Use to identify why specific
           trades were entered or missed.
        """
        # claude code changed: docstring above reworded from hardcoded "AVAX_ATOM_*.csv" filenames to the generic "<PAIR>_*.csv" pattern below (can't mark inline inside the string itself)

        # claude code changed: new block (3 lines below) — dynamic prefix replaces the hardcoded "AVAX_ATOM" string used in every path below
        # Short filename prefix, e.g. "AVAX_ATOM" for AVAX_USDT/ATOM_USDT —
        # matches walk_forward_engine.py's pair_deserves_testing(), which
        # already tries this exact short-name pattern when looking for a
        # Missing-Piece-4 summary to gate on.
        # claude code changed: ".replace('/', '-')" is new — found via Phase
        # 1C's entry_exit_engine.py test coverage (Blocker A/Step 4). An FX
        # symbol like "EUR/USD" (bot/instruments.py's own canonical_symbol
        # example format) passed straight through into a filename made "/"
        # get read as a path separator by pathlib, so
        # output_dir/"EUR/USD_GBP/USD_trade_log.csv" tried to write into
        # nonexistent subdirectories and crashed. Crypto symbols (no "/")
        # are unaffected — this only changes behavior for a pair identity
        # this engine could not previously save output for at all.
        short_a = (self.symbol_a or SYMBOL_A).replace("_USDT", "").replace("/", "-")   # claude code changed: new
        short_b = (self.symbol_b or SYMBOL_B).replace("_USDT", "").replace("/", "-")   # claude code changed: new
        prefix  = f"{short_a}_{short_b}"                             # claude code changed: new

        # Trade log — primary output
        if not trade_log.empty:
            p = self.output_dir / f"{prefix}_trade_log.csv"          # claude code changed: was "AVAX_ATOM_trade_log.csv"
            trade_log.to_csv(p, index=False)
            logger.info(f"  Saved trade log    : {p}")

        # Strategy summary
        if not summary.empty:
            p = self.output_dir / f"{prefix}_strategy_summary.csv"   # claude code changed: was "AVAX_ATOM_strategy_summary.csv"
            summary.to_csv(p, index=False)
            logger.info(f"  Saved summary      : {p}")

        # Equity curve
        if not equity_curve.empty:
            p = self.output_dir / f"{prefix}_equity_curve.csv"       # claude code changed: was "AVAX_ATOM_equity_curve.csv"
            equity_curve.to_csv(p, index=False)
            logger.info(f"  Saved equity curve : {p}")

        # Signal log
        if not signal_log.empty:
            p = self.output_dir / f"{prefix}_signal_log.csv"         # claude code changed: was "AVAX_ATOM_signal_log.csv"
            signal_log.reset_index().to_csv(p, index=False)
            logger.info(f"  Saved signal log   : {p}")


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: PRINT PERFORMANCE REPORT
    # ─────────────────────────────────────────────────────────────────────────

    def _print_performance_report(
        self,
        summary:   pd.DataFrame,
        trade_log: pd.DataFrame,
    ) -> None:
        """
        Print a clean, readable performance report to the terminal.

        This is what you read to know whether the strategy simulation
        passed or failed. The thresholds printed are derived from our
        research and from institutional pairs trading standards.
        """

        if summary.empty:
            logger.warning("No summary to print — no completed trades.")
            return

        s = summary.iloc[0]    # Single row of summary statistics

        # claude code changed: new block (13 lines below) — display stand-ins for baselines that may now be None
        # Human-readable stand-ins for baselines that may not exist for
        # this pair — never silently substitute AVAX/ATOM's numbers here.
        half_life_display = (                                                             # claude code changed: new
            f"{self.validated_half_life:.0f}h" if self.validated_half_life is not None    # claude code changed: new
            else "n/a — no half-life resolved for this pair"                              # claude code changed: new
        )                                                                                   # claude code changed: new
        win_rate_target_display = (                                                        # claude code changed: new
            f"{self.validated_win_rate:.0%}+" if self.validated_win_rate is not None       # claude code changed: new
            else "n/a — no validated win-rate baseline for this pair"                      # claude code changed: new
        )                                                                                   # claude code changed: new
        ic_display = (                                                                      # claude code changed: new
            str(self.validated_ic) if self.validated_ic is not None                        # claude code changed: new
            else "n/a — no validated IC baseline for this pair"                            # claude code changed: new
        )                                                                                    # claude code changed: new

        sep = "=" * 70

        print(f"\n{sep}")
        print(f"{self.pair_name} KALMAN PAIRS STRATEGY — SIMULATION RESULTS")   # claude code changed: was hardcoded "AVAX/ATOM KALMAN PAIRS STRATEGY — SIMULATION RESULTS"
        print(f"QuantiBot Pro — Missing Piece 4 of 6")
        print(sep)

        print(f"\n  Pair            : {s['pair']}")
        print(f"  Simulation period: {trade_log['entry_timestamp'].min()} "
              f"→ {trade_log['exit_timestamp'].max()}")
        print(f"  Initial capital  : ${STRATEGY_CAPITAL_USDT:,.0f}")
        print(f"  Final capital    : ${STRATEGY_CAPITAL_USDT + s['total_pnl_usdt']:,.0f}")
        print(f"  Total P&L        : ${s['total_pnl_usdt']:,.2f} "
              f"({s['total_pnl_pct']:.2%})")

        print(f"\n{'─'*70}")
        print("  TRADE STATISTICS")
        print(f"{'─'*70}")
        print(f"  Total trades     : {s['n_total_trades']:,}")
        print(f"  Winners          : {s['n_winners']:,}")
        print(f"  Losers           : {s['n_losers']:,}")
        print(f"  Win rate         : {s['win_rate']:.1%}  "
              f"(research target: {win_rate_target_display})")   # claude code changed: was f"(research target: {VALIDATED_WIN_RATE:.0%}+)"
        print(f"  Avg winner       : {s['avg_winner_pct']:.4f}")
        print(f"  Avg loser        : {s['avg_loser_pct']:.4f}")
        print(f"  Profit factor    : {s['profit_factor']:.2f}  "
              f"(target: > 1.5)")

        print(f"\n{'─'*70}")
        print("  RISK METRICS")
        print(f"{'─'*70}")
        print(f"  Sharpe ratio     : {s['sharpe_ratio']:.2f}  "
              f"(target: > 1.0)")
        print(f"  Max drawdown     : {s['max_drawdown_pct']:.2%}  "
              f"(target: < 20%)")
        print(f"  Avg hold time    : {s['avg_hold_hours']:.0f}h  "
              f"(half-life: {half_life_display})")   # claude code changed: was f"(half-life: {VALIDATED_HALF_LIFE:.0f}h)"
        print(f"  Median hold time : {s['median_hold_hours']:.0f}h")

        print(f"\n{'─'*70}")
        print("  EXIT BREAKDOWN")
        print(f"{'─'*70}")
        print(f"  Target exits     : {s['exit_target_count']:,}  "
              f"(mean reversion completed)")
        print(f"  Stop loss exits  : {s['exit_stoploss_count']:,}  "
              f"(spread moved against us)")
        print(f"  Time stop exits  : {s['exit_timestop_count']:,}  "
              f"(held > {self.exit_time_stop}h)")
        print(f"  Partial exits    : {s['exit_partial_count']:,}  "
              f"(50% taken at |z|<{self.exit_partial_z})")

        print(f"\n{'─'*70}")
        print("  SIGNAL VALIDATION")
        print(f"{'─'*70}")
        print(f"  Entry IC         : {s['entry_ic']:.4f}  "
              f"(IC between entry |z| and trade P&L)")
        print(f"  Entry IC p-value : {s['entry_ic_pvalue']:.4f}")
        print(f"  Validated IC     : {ic_display}  "        # claude code changed: was f"  Validated IC     : {s['validated_ic']}  "
              f"(from kalman_filter_engine)")

        # ── Verdict ───────────────────────────────────────────────────────────
        print(f"\n{sep}")
        print("  VERDICT")
        print(sep)

        passed  = []    # Metrics that passed threshold
        failed  = []    # Metrics that failed threshold

        if s["win_rate"] >= 0.60:
            passed.append(f"Win rate {s['win_rate']:.1%} ≥ 60%")
        else:
            failed.append(f"Win rate {s['win_rate']:.1%} < 60%")

        if s["sharpe_ratio"] >= 1.0:
            passed.append(f"Sharpe {s['sharpe_ratio']:.2f} ≥ 1.0")
        else:
            failed.append(f"Sharpe {s['sharpe_ratio']:.2f} < 1.0")

        if s["profit_factor"] >= 1.5:
            passed.append(f"Profit factor {s['profit_factor']:.2f} ≥ 1.5")
        else:
            failed.append(f"Profit factor {s['profit_factor']:.2f} < 1.5")

        if s["max_drawdown_pct"] >= -0.20:
            passed.append(f"Max drawdown {s['max_drawdown_pct']:.1%} within 20%")
        else:
            failed.append(f"Max drawdown {s['max_drawdown_pct']:.1%} exceeds 20%")

        for item in passed:
            print(f"  ✓ {item}")
        for item in failed:
            print(f"  ✗ {item}")

        if not failed:
            print(f"\n  ✓✓ ALL THRESHOLDS PASSED")
            print(f"  Strategy is ready for Missing Piece 5 — Walk-Forward Validation")
            print(f"  Run: python -m bot.research.walk_forward_engine")
        elif len(passed) >= len(failed):
            print(f"\n  ⚠ PARTIAL PASS — review failed metrics before proceeding")
            print(f"  Consider adjusting parameters and re-running simulation")
        else:
            print(f"\n  ✗ STRATEGY DID NOT PASS — do not proceed to live trading")
            print(f"  Review entry/exit parameters and re-run")

        print(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_entry_exit_simulation(
    kalman_csv:              str   = "research_data/AVAX_USDT_ATOM_USDT_kalman.csv",
    output_dir:              str   = "research_data",
    capital:                 float = STRATEGY_CAPITAL_USDT,
    require_passes_filters:  bool  = True,   # claude code changed: new param — see docstring
    resample_hours:          Optional[int] = None,   # claude code changed: new param — see CANDLE_RESAMPLE_HOURS module comment
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Standalone entry point for the entry/exit simulation.

    Reads the Kalman filter CSV, runs the complete strategy simulation,
    saves all outputs, and prints the performance report.

    Run from project root:
        python -m bot.research.entry_exit_engine

    Parameters
    ----------
    kalman_csv : str
        Path to Kalman filter output. Must exist before running this.
        Produced by: python -m bot.research.kalman_filter_engine.
        Must follow the "<SYMBOL_A>_<SYMBOL_B>_kalman.csv" naming
        convention that module already saves under — pair identity is
        parsed straight out of this filename, e.g.
        "research_data/DOT_USDT_LINK_USDT_kalman.csv".

    output_dir : str
        Where to save trade log, equity curve, and summary.

    capital : float
        Strategy capital in USDT. Default $10,000.

    require_passes_filters : bool
        Passed straight through to kalman_filter_engine.load_pair_config().
        Default True refuses to look up the half-life for a pair
        cointegration_engine.py rejected (e.g. half-life over its 120h
        ceiling) — the lookup raises and this function falls back to
        AVAX/ATOM's 119.9h with a warning, per the note below. Set to
        False to deliberately test a pair that failed that filter
        (e.g. DOT_USDT/LINK_USDT at 196.8h) using ITS OWN real half-life
        instead of silently substituting AVAX/ATOM's — the two pairs'
        spreads revert at very different speeds, and testing DOT/LINK
        with AVAX/ATOM's half-life would size its time-stop wrong and
        make its sanity check meaningless.

    Returns
    -------
    Tuple[trade_log, summary, equity_curve] DataFrames.
    """
    # claude code changed: the "Must follow..." sentences in the kalman_csv param doc above are new (docstring content, no inline "#" possible)

    # claude code changed: new block (whole block below) — pair identity used to be implicit/hardcoded, now resolved + half-life looked up per pair
    # ── Resolve pair identity from the kalman CSV filename ────────────────
    try:                                                                              # claude code changed: new
        pair_name, symbol_a, symbol_b = _parse_pair_from_kalman_filename(kalman_csv)  # claude code changed: new
    except ValueError as e:                                                           # claude code changed: new
        raise ValueError(                                                             # claude code changed: new
            f"run_entry_exit_simulation() requires a kalman CSV that "                # claude code changed: new
            f"follows kalman_filter_engine.py's "                                     # claude code changed: new
            f"'<SYMBOL_A>_<SYMBOL_B>_kalman.csv' naming convention so the "           # claude code changed: new
            f"pair identity can be resolved. {e}"                                     # claude code changed: new
        ) from e                                                                       # claude code changed: new

    # ── Look up this pair's real half-life ─────────────────────────────────
    # Read straight from cointegration_engine.py's saved output — the same
    # source kalman_filter_engine.py itself reads its hedge-ratio seed
    # from — so the sanity check in _build_strategy_summary() and the
    # report below reflect THIS pair's actual half-life, not AVAX/ATOM's.
    # If the lookup fails (pair not yet in cointegration_pairs.csv, or that
    # file is missing), fall back to AVAX/ATOM's validated 119.9h with a
    # loud warning.
    #
    # VALIDATED_IC and VALIDATED_WIN_RATE deliberately have NO equivalent
    # fallback here. research_data/model_governance_log.md documents that
    # those two AVAX/ATOM numbers were never actually valid to begin with
    # (near-zero/negative entry_ic in every walk-forward fold, permutation
    # test p-values of ~1.0) — so there is no "known good" cross-pair
    # default worth falling back to. They are simply left unset (None)
    # unless a caller supplies pair-specific values explicitly.
    from bot.research.kalman_filter_engine import load_pair_config              # claude code changed: new import — pulls the pair-agnostic lookup added to kalman_filter_engine.py

    try:                                                                         # claude code changed: new
        pair_config = load_pair_config(                                        # claude code changed: was load_pair_config(pair_name) with no require_passes_filters arg
            pair_name, require_passes_filters=require_passes_filters           # claude code changed: new — threads the param above through
        )
        validated_half_life = pair_config["half_life_h"]                        # claude code changed: new
        if not require_passes_filters and not pair_config["passes_filters"]:   # claude code changed: new — make the override visible, not silent
            logger.warning(                                                    # claude code changed: new
                f"'{pair_name}' did NOT pass cointegration_engine.py's own "   # claude code changed: new
                f"filters, but require_passes_filters=False was set, so this " # claude code changed: new
                f"run is using its real half-life ({validated_half_life}h) "  # claude code changed: new
                f"anyway. Treat this as a deliberate test of a rejected "      # claude code changed: new
                f"pair, not a validated one — see model_governance_log.md."   # claude code changed: new
            )                                                                    # claude code changed: new
    except (FileNotFoundError, ValueError) as e:                                # claude code changed: new
        validated_half_life = VALIDATED_HALF_LIFE                               # claude code changed: new — fallback only, not a default assumption
        logger.warning(                                                         # claude code changed: new
            f"Could not look up half-life for '{pair_name}' from "             # claude code changed: new
            f"cointegration_pairs.csv ({e}). Falling back to AVAX/ATOM's "     # claude code changed: new
            f"validated half-life ({VALIDATED_HALF_LIFE}h) — treat the "      # claude code changed: new
            f"half-life sanity check and report below as unreliable for "     # claude code changed: new
            f"this pair until cointegration_engine.py has actually tested it." # claude code changed: new
        )                                                                        # claude code changed: new

    # claude code changed: new — the module header derives EXIT_TIME_STOP_HOURS=240
    # as "2 x half-life of 119.9h", but that derivation was never actually applied
    # per-pair — every pair got the same fixed 240h regardless of its own half-life.
    # For AVAX/ATOM that's a coincidence (2*119.9=239.8~240), not a real derivation.
    # For a pair with a genuinely different half-life (DOT/LINK at 196.8h), reusing
    # 240h as the time-stop is only ~1.2x its half-life, not 2x — cutting positions
    # short before they've had a realistic chance to revert. Deriving it here means
    # every pair actually gets what the original design intended.
    exit_time_stop_hours = round(2 * validated_half_life)   # claude code changed: new

    logger.info("=" * 70)
    logger.info(f"ENTRY/EXIT ENGINE — {pair_name} KALMAN PAIRS STRATEGY")   # claude code changed: was hardcoded "ENTRY/EXIT ENGINE — AVAX/ATOM KALMAN PAIRS STRATEGY"
    logger.info("QuantiBot Pro — Missing Piece 4 of 6")
    logger.info("=" * 70)
    logger.info(f"  Reading   : {kalman_csv}")                              # claude code changed: relabeled "Reading" (was "Reading  :")
    logger.info(f"  Capital   : ${capital:,.0f}")                           # claude code changed: relabeled "Capital" (was "Capital  :")
    logger.info(f"  Time stop : {exit_time_stop_hours}h (2 x half-life)")   # claude code changed: new
    logger.info(f"  Half-life : {validated_half_life}h")                    # claude code changed: was two separate lines logging hardcoded VALIDATED_IC / VALIDATED_WIN_RATE

    # claude code changed: comment + constructor args below are new — pair identity/half-life now threaded into the engine instead of relying on module constants
    # Initialise the engine. validated_ic/validated_win_rate are
    # intentionally left unset (None) — see note above.
    engine = EntryExitEngine(
        pair_name=pair_name,                            # claude code changed: new arg
        symbol_a=symbol_a,                               # claude code changed: new arg
        symbol_b=symbol_b,                               # claude code changed: new arg
        validated_half_life=validated_half_life,        # claude code changed: new arg
        exit_time_stop_hours=exit_time_stop_hours,      # claude code changed: new arg — was implicitly EXIT_TIME_STOP_HOURS=240 for every pair
        resample_hours=resample_hours,                   # claude code changed: new arg
        capital_usdt=capital,
        output_dir=output_dir,
    )

    # Run simulation
    trade_log, summary, equity_curve = engine.run(kalman_csv=kalman_csv)

    logger.info("\n" + "=" * 70)
    logger.info("NEXT STEPS IN THE QUANTIBOT PRO PIPELINE:")
    logger.info("=" * 70)
    logger.info(
        "\n  If all thresholds passed above:"
        "\n  Missing Piece 5 — Walk-Forward Validation"
        "\n  python -m bot.research.walk_forward_engine"
        "\n"
        "\n  Walk-forward tests the strategy on UNSEEN DATA:"
        "\n  Train on 2020-2022 → test on 2023"
        "\n  Train on 2020-2023 → test on 2024"
        "\n  Train on 2020-2024 → test on 2025"
        "\n"
        "\n  If walk-forward passes → Missing Piece 6 (live monitoring)"
        "\n  Then: paper trade 6 weeks before real capital"
    )

    return trade_log, summary, equity_curve


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.entry_exit_engine
        python -m bot.research.entry_exit_engine --kalman-csv research_data/DOT_USDT_LINK_USDT_kalman.csv --allow-filtered-pair
    """
    # claude code changed: the second "python -m ..." example line above is new (string literal — no inline "#" possible); everything below in this block is new, replacing a plain 5-line run_entry_exit_simulation(...) call with hardcoded args
    import argparse                                                              # claude code changed: new

    parser = argparse.ArgumentParser(                                           # claude code changed: new
        description=(                                                           # claude code changed: new
            "Entry/exit rules engine — simulates the pairs-trading "           # claude code changed: new
            "strategy on one pair's Kalman filter output. Pair identity "      # claude code changed: new
            "is parsed from --kalman-csv's filename, so this works on any "    # claude code changed: new
            "pair kalman_filter_engine.py has produced output for, not "       # claude code changed: new
            "just AVAX/ATOM."                                                   # claude code changed: new
        )                                                                        # claude code changed: new
    )                                                                            # claude code changed: new
    parser.add_argument(                                                        # claude code changed: new
        "--kalman-csv",                                                         # claude code changed: new
        type=str,                                                               # claude code changed: new
        default="research_data/AVAX_USDT_ATOM_USDT_kalman.csv",                # claude code changed: new
        help=(                                                                  # claude code changed: new
            "Path to kalman_filter_engine.py's output CSV for the pair to "    # claude code changed: new
            "simulate, e.g. research_data/DOT_USDT_LINK_USDT_kalman.csv. "     # claude code changed: new
            "Must follow the '<SYMBOL_A>_<SYMBOL_B>_kalman.csv' naming "       # claude code changed: new
            "convention that module already saves under."                      # claude code changed: new
        ),                                                                       # claude code changed: new
    )                                                                            # claude code changed: new
    parser.add_argument(                                                        # claude code changed: new
        "--output-dir",                                                         # claude code changed: new
        type=str,                                                               # claude code changed: new
        default="research_data",                                                # claude code changed: new
        help="Directory to save the trade log, summary, equity curve, and signal log.",  # claude code changed: new
    )                                                                            # claude code changed: new
    parser.add_argument(                                                        # claude code changed: new
        "--capital",                                                            # claude code changed: new
        type=float,                                                             # claude code changed: new
        default=STRATEGY_CAPITAL_USDT,                                          # claude code changed: new
        help=f"Strategy capital in USDT (default: {STRATEGY_CAPITAL_USDT:,.0f}).",  # claude code changed: new
    )                                                                            # claude code changed: new
    parser.add_argument(                                                        # claude code changed: new
        "--allow-filtered-pair",                                                # claude code changed: new
        action="store_true",                                                    # claude code changed: new
        help=(                                                                  # claude code changed: new
            "Test a pair even though cointegration_engine.py rejected it "     # claude code changed: new
            "(e.g. half-life over the 120h ceiling), using ITS OWN real "      # claude code changed: new
            "half-life instead of silently falling back to AVAX/ATOM's. "      # claude code changed: new
            "Without this flag, such a pair falls back to AVAX/ATOM's "        # claude code changed: new
            "119.9h half-life with a warning — wrong for a slow-reverting "    # claude code changed: new
            "pair like DOT_USDT/LINK_USDT (196.8h)."                           # claude code changed: new
        ),                                                                       # claude code changed: new
    )                                                                            # claude code changed: new
    parser.add_argument(                                                        # claude code changed: new
        "--resample-hours",                                                    # claude code changed: new
        type=int,                                                               # claude code changed: new
        default=None,                                                          # claude code changed: new
        help=(                                                                  # claude code changed: new
            "Resample the kalman series to this candle width (in hours) "      # claude code changed: new
            "before scanning for entries/exits, e.g. 4. Default: no "          # claude code changed: new
            "resampling (native 1h candles)."                                   # claude code changed: new
        ),                                                                       # claude code changed: new
    )                                                                            # claude code changed: new
    args = parser.parse_args()                                                  # claude code changed: new

    run_entry_exit_simulation(                                                  # claude code changed: was called with hardcoded kalman_csv/output_dir/capital values
        kalman_csv=args.kalman_csv,                                            # claude code changed: was "research_data/AVAX_USDT_ATOM_USDT_kalman.csv"
        output_dir=args.output_dir,                                            # claude code changed: was "research_data"
        capital=args.capital,                                                   # claude code changed: was STRATEGY_CAPITAL_USDT
        require_passes_filters=not args.allow_filtered_pair,                   # claude code changed: new
        resample_hours=args.resample_hours,                                    # claude code changed: new
    )