# ============================================================
# bot/pairs/config.py
# claude code changed: new file — live pairs-trading pilot, Step 1.
# claude code changed: expanded 2026-09-28 from the original 3-pair
# leg-disjoint portfolio to 10 monitored pairs (user's explicit request
# to watch every pair the dashboard's pair-selector already listed) — 2
# of the 10 (DODO/FIDA, MINA/ONG) are currently entry-eligible (real
# capital, can trade); the other 8 are monitoring-only (real, live
# Kalman beta tracking + NORMAL/DEGRADED/INVALIDATED health, but
# capital_usdt=0.0 and is_entry_eligible=False — see PairConfig's own
# field comments, _load_validated_win_rate(), and
# bot/pairs/monitor_engine.py).
#
# Static configuration for the pilot portfolio. The original 3-pair
# leg-disjoint design (DODO/FIDA, MINA/ONG, AVA/PHA) is the ONLY design
# in this project's research history to survive permutation testing, the
# leg-overlap check, AND real (not modeled) order-book cost measurement.
# See research_data/model_governance_log.md's "3-pair leg-disjoint
# subset re-tested" entry (2026-09-19) and the "Eighth design attempt"
# entry for where every entry-eligible parameter below comes from. Of
# the original 3, AVA/PHA has since started failing the live
# cointegration gate (half-life now exceeds the max) and is monitoring-
# only as of this expansion — not removed, so its degraded status stays
# visible rather than silently disappearing from the dashboard.
#
# hedge_ratio/intercept are the OLS seed values from
# research_data/cointegration_pairs.csv (read once, at import time, so
# this file can never silently drift from the authoritative scan) —
# these seed the online Kalman filter's initial state exactly the way
# KalmanFilterEngine._initialise_state() seeds the offline/batch one.
# Computed for every pair regardless of whether it currently passes
# filters (an OLS fit happens before the cointegration filter is
# applied), so every pair — including the 8 monitoring-only ones — has a
# real, non-placeholder seed beta for PairMonitorEngine to track.
#
# validated_win_rate is read from each entry-eligible pair's own
# research_data/permutation_test/<PAIR>/permutation_verdict.csv at import
# time (never hardcoded) — KalmanPositionSizer requires a real,
# pair-specific win rate and raises if one isn't supplied (see
# entry_exit_engine.py's own validation, added after the AVAX/ATOM
# cross-pair-default bug). For a monitoring-only pair this is None by
# design (see _load_validated_win_rate()) — PairMonitorEngine never
# constructs a KalmanPositionSizer at all, so there is nothing for a win
# rate to feed.
#
# MIN_POSITION_USDT_OVERRIDE (15.0) deliberately departs from
# entry_exit_engine.py's module-level MIN_POSITION_USDT (100.0), which is
# calibrated for the $10k/leg backtest reference size and would zero out
# every trade at this pilot's real per-pair capital. $15 = 3x Binance's
# real $5 minNotional floor (confirmed live via ccxt for all 6 original
# symbols), leaving buffer for fee/slippage/precision rounding.
#
# claude code changed: KalmanPositionSizer.size_position() (in
# entry_exit_engine.py) has its OWN internal floor check that runs
# BEFORE any downstream caller ever sees the result — a bare module-
# constant comparison this pilot's own code could never have overridden
# from outside. Caught by bot/core/dry_run_test.py's section 14
# (a sizing call at real pilot capital returned exactly $0.00). Fixed at
# the source: entry_exit_engine.py's KalmanPositionSizer/EntryExitEngine
# now both take min_position_usdt as a real, optional constructor
# parameter (default unchanged, so every existing caller/test is
# unaffected) — this value is passed through from here into
# signal_engine.py's EntryExitEngine(...) construction. See
# bot/pairs/signal_engine.py's own downstream check for why it's now a
# secondary, defensive check rather than the actual enforcement.
# ============================================================

import csv
import os
from dataclasses import dataclass
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COINTEGRATION_CSV = os.path.join(BASE_DIR, "research_data", "cointegration_pairs.csv")
PERMUTATION_DIR = os.path.join(BASE_DIR, "research_data", "permutation_test")

# claude code changed: was 100.0 ($33.33/pair) — bot/core/dry_run_test.py's
# section 14 caught a real capacity problem this pilot's own
# MAX_POSITION_FRACTION (0.30, from entry_exit_engine.py, unchanged/validated)
# creates at that capital: the largest possible single trade at $33.33
# capital is $10 total, and every one of the 3 pairs' hedge ratios splits
# that unevenly enough that at least one leg comes out below Binance's
# real $5 minNotional (confirmed live) — DODO/FIDA leg_b=$4.43, MINA/ONG
# leg_a=$3.75, AVA/PHA leg_b=$4.22. No MIN_POSITION_USDT floor value can
# fix this — it's a ceiling problem (MAX_POSITION_FRACTION), not a floor
# problem. $300 ($100/pair) keeps the validated 30% cap and all 3 pairs
# untouched; worst case (MINA/ONG, $30 max total at 30%) gives leg_a=
# $11.27/leg_b=$18.73 — both comfortably clear $5. User's explicit choice
# among three options (raise capital / trade fewer pairs / override the
# cap) when this was surfaced.
#
# claude code changed: raised 300.0 -> 500.0 (user's explicit request,
# 2026-09-26). Raising capital only ever widens the minNotional margin
# from the $300 case above (worst leg was already $11.27, comfortably
# above Binance's $5 floor) — no re-verification of the floor problem is
# needed the way it was when capital was lowered from $100. At $500
# ($166.67/pair), MINA/ONG's worst case (30% cap = $50 total) gives
# leg_a=$18.79/leg_b=$31.21 — both scale up proportionally, still clears.
# Requires a pairs_bot_runner.py restart to take effect — bot/pairs/
# config.py's module-level constants are read once at process import.
TOTAL_PILOT_CAPITAL_USDT = 500.0

# claude code changed: expanded from 3 to 10 pairs — user's explicit request
# to monitor every pair the dashboard's pair-selector already lists
# (AVAX_ATOM, AVA_PHA, DEXE_ALGO, DODO_FIDA, DOT_LINK, FET_BCH, FET_DASH,
# MINA_ONG, ONT_RIF, SOL_MASK), with real NORMAL/DEGRADED/INVALIDATED
# tagging for each — not "make all 10 tradeable." Verified directly
# against a fresh cointegration_pairs.csv (research_data/model_governance_log.md
# has the full story): only DODO_USDT/FIDA_USDT and MINA_USDT/ONG_USDT
# currently pass the real cointegration gate. The other 8 all have a
# permutation_verdict.csv claiming edge_appears_real=True, but that data
# is STALE — a pair that isn't cointegrated cannot have a genuine
# permutation-tested spread edge; these verdicts predate this project's
# later out-of-sample persistence check (which retroactively rejected 76
# of 84 previously-passing pairs elsewhere in this codebase's history).
# _load_validated_win_rate() below now checks the CURRENT cointegration
# status first and refuses to trust a stale permutation file for a pair
# that currently fails it. DEXE_USDT/ALGO_USDT, DOT_USDT/LINK_USDT, and
# FET_USDT/BCH_USDT were never in cointegration_pairs.csv at all before
# now — real, fresh cointegration tests were run for exactly these 3
# pairs and appended to the authoritative CSV (2026-09-28), not fabricated.
PAIR_NAMES = [
    "DODO_USDT/FIDA_USDT",
    "MINA_USDT/ONG_USDT",
    "AVA_USDT/PHA_USDT",
    "AVAX_USDT/ATOM_USDT",
    "DEXE_USDT/ALGO_USDT",
    "DOT_USDT/LINK_USDT",
    "FET_USDT/BCH_USDT",
    "FET_USDT/DASH_USDT",
    "ONT_USDT/RIF_USDT",
    "SOL_USDT/MASK_USDT",
]

MIN_POSITION_USDT_OVERRIDE = 15.0  # see module docstring above — NOT entry_exit_engine.py's MIN_POSITION_USDT. Re-verified clearable at $100/pair capital (see dry_run_test.py section 14) after the TOTAL_PILOT_CAPITAL_USDT fix above.
DISABLE_TARGET_EXIT = True
EXIT_TIME_STOP_HOURS = 4  # fixed, per the validated "Eighth design attempt" — NOT auto-derived from half-life
KELLY_SAFETY_FRACTION = 0.25  # matches entry_exit_engine.py's own default; kept explicit here for visibility


@dataclass(frozen=True)
class PairConfig:
    pair_name: str          # "DODO_USDT/FIDA_USDT" — matches cointegration_pairs.csv's pair_name column
    symbol_a: str            # "DODO_USDT"
    symbol_b: str            # "FIDA_USDT"
    binance_symbol_a: str    # "DODO/USDT" — ccxt unified format
    binance_symbol_b: str    # "FIDA/USDT"
    hedge_ratio: float       # OLS seed beta, from cointegration_pairs.csv — real for ALL 10 pairs, whether they pass filters or not (an OLS fit happens before filtering)
    intercept: float         # OLS seed alpha, from cointegration_pairs.csv
    validated_win_rate: Optional[float]  # claude code changed: now Optional — None for a pair that currently fails cointegration or has no trustworthy (non-stale) permutation verdict; see _load_validated_win_rate()
    capital_usdt: float      # claude code changed: 0.0 for a monitoring-only pair (see is_entry_eligible) — capital is never allocated to a pair that structurally cannot trade
    passes_filters: bool     # claude code changed: new — cointegration_pairs.csv's current static-validation status. Deliberately NOT a load-time gate here (config.py stays permissive so every configured pair can still run and be observed, e.g. AVA_PHA kept active for comparison after it started failing) — bot/pairs/health.py is where this becomes an actual NORMAL/DEGRADED/INVALIDATED decision.
    reject_reason: str       # "" if passes_filters is True — real text from cointegration_pairs.csv otherwise
    is_entry_eligible: bool  # claude code changed: new — True only if validated_win_rate is not None. Drives whether pairs_bot_runner.py constructs a full PairSignalEngine (can trade) or a PairMonitorEngine (tracking only, structurally cannot trade — see bot/pairs/monitor_engine.py)


def _to_binance_symbol(underscore_symbol: str) -> str:
    """"DODO_USDT" -> "DODO/USDT" (ccxt unified format)."""
    base, quote = underscore_symbol.rsplit("_", 1)
    return f"{base}/{quote}"


def _load_cointegration_row(pair_name: str) -> dict:
    with open(COINTEGRATION_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if row["pair_name"] == pair_name:
                return row
    raise ValueError(
        f"{pair_name} not found in {COINTEGRATION_CSV} — cannot seed Kalman filter "
        f"without a real, current OLS hedge_ratio/intercept for this pair."
    )


# claude code changed: capital is split ONLY among pairs that currently
# pass the real cointegration gate (today: DODO/FIDA + MINA/ONG, same 2
# pairs as before this expansion) — NOT divided across all 10. The other
# 8 are monitoring-only (see bot/pairs/monitor_engine.py) and will never
# construct a position sizer at all, so allocating them capital would
# just be $ sitting permanently unused while diluting the 2 pairs that
# actually trade. This preserves the already-verified $166.67/pair
# minNotional-floor headroom (dry_run_test.py section 14) exactly as it
# was before this change — re-verified, not assumed, post-change (see
# dry_run_test.py's own section 14 output after this edit).
def _passes_filters_now(pair_name: str) -> bool:
    try:
        row = _load_cointegration_row(pair_name)
    except ValueError:
        return False
    return str(row.get("passes_filters")) == "True"


ENTRY_ELIGIBLE_PAIR_NAMES = [p for p in PAIR_NAMES if _passes_filters_now(p)]
PER_PAIR_CAPITAL_USDT = (
    TOTAL_PILOT_CAPITAL_USDT / len(ENTRY_ELIGIBLE_PAIR_NAMES) if ENTRY_ELIGIBLE_PAIR_NAMES else 0.0
)  # equal-weighted, matches the validated backtest convention — but only across entry-eligible pairs now


def _load_validated_win_rate(pair_name: str) -> Optional[float]:
    """
    claude code changed: real bug fix — this used to raise on any pair
    whose permutation_verdict.csv didn't say edge_appears_real=True, and
    NEVER checked the pair's current cointegration status before opening
    that file at all. That made it possible for a STALE permutation
    verdict (edge_appears_real=True, computed before this project added
    the out-of-sample persistence check) to silently produce a real win
    rate for a pair the CURRENT scan actually rejects — confirmed this
    is a real, live problem for 5 of the 10 pairs this pilot now
    monitors (AVAX/ATOM, AVA/PHA, FET/DASH, ONT/RIF, SOL/MASK all have
    edge_appears_real=True on file despite currently failing
    cointegration outright).

    Now returns None (not a raised exception) — for a monitoring-only
    pair, "no real validated win rate" is an expected, normal state, not
    an error. The CURRENT cointegration status is checked FIRST, before
    the permutation file is ever opened, so a stale file can never
    override the live gate.
    """
    coint_row = _load_cointegration_row(pair_name)
    if str(coint_row.get("passes_filters")) != "True":
        return None

    folder_name = pair_name.replace("/", "_")  # "DODO_USDT/FIDA_USDT" -> "DODO_USDT_FIDA_USDT", matches research_data/permutation_test/'s on-disk convention
    verdict_path = os.path.join(PERMUTATION_DIR, folder_name, "permutation_verdict.csv")
    if not os.path.exists(verdict_path):
        return None
    with open(verdict_path, newline="") as f:
        row = next(csv.DictReader(f))
        if row.get("edge_appears_real") != "True":
            return None
        return float(row["win_rate"])


def _build_pair_config(pair_name: str) -> PairConfig:
    symbol_a, symbol_b = pair_name.split("/")
    coint_row = _load_cointegration_row(pair_name)
    win_rate = _load_validated_win_rate(pair_name)
    is_entry_eligible = win_rate is not None
    return PairConfig(
        pair_name=pair_name,
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        binance_symbol_a=_to_binance_symbol(symbol_a),
        binance_symbol_b=_to_binance_symbol(symbol_b),
        hedge_ratio=float(coint_row["hedge_ratio"]),
        intercept=float(coint_row["intercept"]),
        validated_win_rate=win_rate,
        capital_usdt=PER_PAIR_CAPITAL_USDT if is_entry_eligible else 0.0,
        passes_filters=str(coint_row.get("passes_filters")) == "True",
        reject_reason=coint_row.get("reject_reason") or "",
        is_entry_eligible=is_entry_eligible,
    )


def load_pair_configs() -> List[PairConfig]:
    """Loads all 10 monitored pairs' configs fresh from the authoritative
    CSVs — 2 currently entry-eligible (DODO/FIDA, MINA/ONG), 8
    monitoring-only.

    Raises loudly only if a pair is missing from cointegration_pairs.csv
    ENTIRELY (a real data gap, not a validation failure) — a pair that's
    present but fails cointegration, or has a missing/untrustworthy
    permutation verdict, now loads successfully with
    is_entry_eligible=False rather than crashing the whole pilot. See
    feedback memory "verify, don't trust comments/cached CSVs" for why
    stale artifacts are treated as a real, repeated failure mode here —
    _load_validated_win_rate() is the concrete fix for that this time.
    """
    return [_build_pair_config(name) for name in PAIR_NAMES]
