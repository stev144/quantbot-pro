# ============================================================
# bot/pairs/config.py
# claude code changed: new file — live pairs-trading pilot, Step 1.
#
# Static configuration for the 3-pair leg-disjoint portfolio (DODO/FIDA,
# MINA/ONG, AVA/PHA) that is the ONLY design in this project's research
# history to survive permutation testing, the leg-overlap check, AND real
# (not modeled) order-book cost measurement. See
# research_data/model_governance_log.md's "3-pair leg-disjoint subset
# re-tested" entry (2026-09-19) and the "Eighth design attempt" entry for
# where every parameter below comes from.
#
# hedge_ratio/intercept are the OLS seed values from
# research_data/cointegration_pairs.csv (read once, at import time, so
# this file can never silently drift from the authoritative scan) —
# these seed the online Kalman filter's initial state exactly the way
# KalmanFilterEngine._initialise_state() seeds the offline/batch one.
#
# validated_win_rate is read from each pair's own
# research_data/permutation_test/<PAIR>/permutation_verdict.csv at import
# time (never hardcoded) — KalmanPositionSizer requires a real,
# pair-specific win rate and raises if one isn't supplied (see
# entry_exit_engine.py's own validation, added after the AVAX/ATOM
# cross-pair-default bug).
#
# MIN_POSITION_USDT_OVERRIDE (15.0) deliberately departs from
# entry_exit_engine.py's module-level MIN_POSITION_USDT (100.0), which is
# calibrated for the $10k/leg backtest reference size and would zero out
# every trade at this pilot's ~$33/pair capital. $15 = 3x Binance's real
# $5 minNotional floor (confirmed live via ccxt for all 6 symbols),
# leaving buffer for fee/slippage/precision rounding.
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
TOTAL_PILOT_CAPITAL_USDT = 300.0
PAIR_NAMES = [
    "DODO_USDT/FIDA_USDT",
    "MINA_USDT/ONG_USDT",
    "AVA_USDT/PHA_USDT",
]
PER_PAIR_CAPITAL_USDT = TOTAL_PILOT_CAPITAL_USDT / len(PAIR_NAMES)  # equal-weighted, matches the validated backtest convention

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
    hedge_ratio: float       # OLS seed beta, from cointegration_pairs.csv
    intercept: float         # OLS seed alpha, from cointegration_pairs.csv
    validated_win_rate: float  # from permutation_verdict.csv, real per-pair value
    capital_usdt: float


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


def _load_validated_win_rate(pair_name: str) -> float:
    folder_name = pair_name.replace("/", "_")  # "DODO_USDT/FIDA_USDT" -> "DODO_USDT_FIDA_USDT", matches research_data/permutation_test/'s on-disk convention
    verdict_path = os.path.join(PERMUTATION_DIR, folder_name, "permutation_verdict.csv")
    with open(verdict_path, newline="") as f:
        row = next(csv.DictReader(f))
        if row.get("edge_appears_real") != "True":
            raise ValueError(
                f"{pair_name}'s permutation_verdict.csv reports edge_appears_real="
                f"{row.get('edge_appears_real')!r} — refusing to build a live config "
                f"for a pair whose own validation file says it isn't validated."
            )
        return float(row["win_rate"])


def _build_pair_config(pair_name: str) -> PairConfig:
    symbol_a, symbol_b = pair_name.split("/")
    coint_row = _load_cointegration_row(pair_name)
    win_rate = _load_validated_win_rate(pair_name)
    return PairConfig(
        pair_name=pair_name,
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        binance_symbol_a=_to_binance_symbol(symbol_a),
        binance_symbol_b=_to_binance_symbol(symbol_b),
        hedge_ratio=float(coint_row["hedge_ratio"]),
        intercept=float(coint_row["intercept"]),
        validated_win_rate=win_rate,
        capital_usdt=PER_PAIR_CAPITAL_USDT,
    )


def load_pair_configs() -> list:
    """Loads all 3 pilot pairs' configs fresh from the authoritative CSVs.

    Raises loudly (does not fall back to a cached/hardcoded value) if any
    pair's cointegration row or permutation verdict is missing or no
    longer marked edge_appears_real=True — this is deliberate: if the
    underlying research data changes, this pilot must not keep trading on
    a stale assumption. See feedback memory "verify, don't trust
    comments/cached CSVs" for why this project treats stale artifacts as
    a real, repeated failure mode.
    """
    return [_build_pair_config(name) for name in PAIR_NAMES]
