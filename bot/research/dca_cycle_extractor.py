# bot/research/dca_cycle_extractor.py
"""
DCA Cycle Research Dataset Extractor — Phase 1 + Phase 2.

Research unit: one completed DCA cycle (non-overlapping by simulation design).
Unique ID:     (pair, direction, entry_time) — exact string as stored in trade dict.
Feature time:  DECISION candle = entry_time − 1 h.
               Gate 1 rationale: the simulation loop queues OPEN at the CLOSE of
               candle i and executes at the OPEN of candle i+1; entry_candle = i+1.
               ADX and regime features at candle i are fully available at decision
               time. Using the fill candle (i+1) would introduce a 1-bar look-ahead.
Join policy:   exact UTC timestamp match; no nearest-neighbour fallback.
               Unmatched cycles are reported and excluded — not silently dropped.
Censored:      cycles with exit_reason == DATA_END_STILL_OPEN are excluded from
               the primary analysis, counted and reported.
Gate 5:        provenance field included in every returned AlignmentResult.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]

# ── pre-declared feature family for entry-time DCA validation ────────────────
# Gate 5: declared BEFORE running any confirmatory test.
# These are the features available at the decision candle that will be tested
# against cycle outcomes. Includes the primary DCA regime inputs (adx, z_score,
# ema_spread_pct) plus structural features added 2026-10-18.
ENTRY_FEATURE_FAMILY: List[str] = [
    # Primary DCA regime/entry inputs — the features the strategy uses
    "adx",
    "z_score",
    "ema_spread_pct",
    # Structural mean-reversion features
    "autocorr_lag1",
    "variance_ratio_4",
    "range_position_24h",
    "realized_vol_ratio",
    # Volatility and momentum indicators
    "atr_ratio",
    "rsi",
    "bb_width",
    # Session timing
    "is_asian_session",
    "is_london_session",
    "day_of_week",
]  # claude code changed: new — pre-declared feature family for DCA entry-time validation

# Primary and secondary cycle outcome targets
TARGET_PRIMARY      = "profit_usdt"          # continuous net P&L after all costs
TARGET_BINARY       = "win"                  # profit_usdt > 0
TARGET_FILLS        = "fills"                # deepest ladder level reached (1-7)
TARGET_DURATION     = "candles_held"         # cycle duration in 1h candles

CENSORED_REASON     = "DATA_END_STILL_OPEN"  # exit_reason value for censored cycles


@dataclass
class AlignmentReport:
    """Per-pair alignment statistics. Gate 5: all exclusions documented."""
    pair:             str
    total_cycles:     int
    closed_cycles:    int
    censored_cycles:  int   # DATA_END_STILL_OPEN — excluded from primary
    matched:          int   # decision candle found in observations CSV
    unmatched:        int   # decision candle timestamp missing from CSV
    match_rate_pct:   float
    obs_row_count:    int   # total rows in the observations CSV for this pair
    obs_date_range:   str   # first–last timestamp in observations CSV
    feature_null_pct: Dict[str, float] = field(default_factory=dict)  # NaN rate per feature


@dataclass
class AlignmentResult:
    """Full extraction result. Gate 5: provenance and exclusion summary."""
    cycle_df:    pd.DataFrame          # cycle-level research dataset (closed+matched)
    reports:     List[AlignmentReport]  # per-pair alignment stats
    pairs_found: List[str]             # pairs that produced at least one matched cycle
    total_matched:   int
    total_censored:  int
    total_unmatched: int
    feature_family:  List[str]


# ── internal helpers ──────────────────────────────────────────────────────────

def _parse_entry_time(entry_time_str: str) -> pd.Timestamp:
    """Parse entry_time string to UTC Timestamp. Raises on invalid input."""
    ts = pd.Timestamp(entry_time_str)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts  # claude code changed: new — timezone-normalised parse for entry_time strings


def _decision_candle_ts(entry_time_str: str, freq_hours: int = 1) -> pd.Timestamp:
    """
    Decision candle timestamp = entry_time − freq_hours.
    Gate 1: the ranging_mask check runs at the CLOSE of the candle before the
    fill; all features at that candle are available at decision time.
    """
    fill_ts = _parse_entry_time(entry_time_str)
    return fill_ts - pd.Timedelta(hours=freq_hours)  # claude code changed: new — 1-bar look-ahead prevention


def _load_observations(pair: str) -> Tuple[pd.DataFrame, str]:
    """
    Load the observations CSV for a pair. Returns (df, path_used).
    Timestamp is normalised to UTC and set as the index.
    Returns empty DataFrame if file not found.
    """
    pair_key = pair.replace("/", "_")
    path = _ROOT / "research_data" / f"{pair_key}_observations.csv"
    if not path.exists():
        logger.warning("dca_extractor: observations CSV not found: %s", path)
        return pd.DataFrame(), str(path)

    df = pd.read_csv(path)

    # Normalise timestamp to UTC index
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()
    elif isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    return df, str(path)  # claude code changed: new — observation CSV loader with UTC normalisation


# ── main extractor ────────────────────────────────────────────────────────────

def extract_dca_cycles(
    pairs: Optional[List[str]] = None,
    freq_hours: int = 1,
    _sim_fn=None,           # injectable for tests — avoids patching deferred imports
    _obs_fn=None,           # injectable for tests — avoids patching _load_observations
) -> AlignmentResult:
    """
    Extract cycle-level research dataset for DCA feature validation.

    Steps
    -----
    1. Run run_ranging_paper_simulation() to get cycle records for all QC pairs.
    2. For each closed cycle: compute decision_candle_ts = entry_time − 1h.
    3. Join to observations CSV on exact decision_candle_ts.
    4. Assemble cycle-level DataFrame with entry features + outcome targets.
    5. Report alignment statistics per pair.

    Parameters
    ----------
    pairs:      subset of QC_PORTFOLIO_SPECS to process (default = all)
    freq_hours: candle frequency in hours (default 1h)
    _sim_fn:    override for run_ranging_paper_simulation (tests only)
    _obs_fn:    override for _load_observations(pair) (tests only)

    Returns
    -------
    AlignmentResult with cycle_df and per-pair reports.
    """
    if _sim_fn is None:
        # deferred import — paper engine imports are heavy
        from bot.paper_trading.ranging_paper_engine import run_ranging_paper_simulation  # claude code changed: deferred
        _sim_fn = run_ranging_paper_simulation

    logger.info("dca_extractor: running paper simulation to collect cycle records…")
    sim_results = _sim_fn()
    logger.info("dca_extractor: simulation complete — %d pairs", len(sim_results))

    all_rows: List[Dict] = []
    reports:  List[AlignmentReport] = []
    pairs_found: List[str] = []

    for pair, data in sim_results.items():
        if pairs is not None and pair not in pairs:
            continue

        trades_raw: List[Dict] = data.get("trades", [])
        if not trades_raw:
            logger.warning("dca_extractor: no cycles for %s", pair)
            continue

        # Separate closed vs censored
        closed   = [t for t in trades_raw if t.get("exit_reason") != CENSORED_REASON]
        censored = [t for t in trades_raw if t.get("exit_reason") == CENSORED_REASON]

        # Load observations CSV
        obs_load_fn = _obs_fn if _obs_fn is not None else _load_observations
        obs_df, obs_path = obs_load_fn(pair)
        if obs_df.empty:
            logger.warning("dca_extractor: no observations for %s — skipping", pair)
            reports.append(AlignmentReport(
                pair=pair, total_cycles=len(trades_raw),
                closed_cycles=len(closed), censored_cycles=len(censored),
                matched=0, unmatched=len(closed), match_rate_pct=0.0,
                obs_row_count=0, obs_date_range="N/A",
            ))
            continue

        obs_range = (
            f"{obs_df.index[0].date()} → {obs_df.index[-1].date()}"
            if len(obs_df) > 0 else "empty"
        )

        # Join closed cycles to decision-candle features
        matched_rows = []
        unmatched_count = 0

        for t in closed:
            try:
                decision_ts = _decision_candle_ts(t["entry_time"], freq_hours=freq_hours)
            except Exception as e:
                logger.warning("dca_extractor: bad entry_time '%s' for %s: %s",
                               t.get("entry_time"), pair, e)
                unmatched_count += 1
                continue

            # Gate 2: exact match only
            if decision_ts not in obs_df.index:
                unmatched_count += 1
                logger.debug(
                    "dca_extractor: decision_ts %s not in obs for %s (entry_time=%s)",
                    decision_ts, pair, t["entry_time"],
                )
                continue

            obs_row = obs_df.loc[decision_ts]

            # Build the cycle research record
            row: Dict = {
                # ── identifiers ──
                "pair":       pair,
                "direction":  t.get("direction", "LONG"),
                "entry_time": t["entry_time"],
                "exit_time":  t.get("exit_time", ""),
                "decision_ts": str(decision_ts),
                # ── entry-time features (at decision candle) ──
            }

            for feat in ENTRY_FEATURE_FAMILY:
                if feat in obs_row.index:
                    val = obs_row[feat]
                    row[feat] = float(val) if pd.notna(val) else np.nan
                else:
                    row[feat] = np.nan

            # ── outcome targets ──
            profit = float(t["profit_usdt"])
            row[TARGET_PRIMARY]  = profit
            row[TARGET_BINARY]   = int(profit > 0)        # 1=win 0=loss; int not bool (Gate 3: IC needs numeric)
            row[TARGET_FILLS]    = int(t["fills"])
            row[TARGET_DURATION] = int(t["candles_held"])
            row["exit_reason"]   = t.get("exit_reason", "")
            row["cost_complete"] = bool(t.get("cost_complete", False))
            row["wap"]           = float(t.get("wap", 0.0))
            row["total_cost_usdt"] = float(t.get("total_cost_usdt", 0.0))

            # optional detailed cost fields if available
            for cost_key in ("gross_usdt", "commission_usdt", "swap_pnl_usdt"):
                if cost_key in t:
                    row[cost_key] = float(t[cost_key])

            matched_rows.append(row)

        matched = len(matched_rows)
        match_rate = round(matched / len(closed) * 100, 1) if closed else 0.0

        if matched < 50:
            logger.warning(
                "dca_extractor: LOW MATCH RATE for %s — %d/%d closed cycles matched (%.0f%%)",
                pair, matched, len(closed), match_rate,
            )

        # Feature null rates for matched rows
        feat_null: Dict[str, float] = {}
        if matched_rows:
            feat_df_tmp = pd.DataFrame(matched_rows)
            for feat in ENTRY_FEATURE_FAMILY:
                if feat in feat_df_tmp.columns:
                    feat_null[feat] = round(feat_df_tmp[feat].isna().mean() * 100, 1)

        report = AlignmentReport(
            pair=pair,
            total_cycles=len(trades_raw),
            closed_cycles=len(closed),
            censored_cycles=len(censored),
            matched=matched,
            unmatched=unmatched_count,
            match_rate_pct=match_rate,
            obs_row_count=len(obs_df),
            obs_date_range=obs_range,
            feature_null_pct=feat_null,
        )
        reports.append(report)

        if matched_rows:
            all_rows.extend(matched_rows)
            pairs_found.append(pair)
            logger.info(
                "dca_extractor: %s — %d closed, %d matched (%.0f%%), %d censored",
                pair, len(closed), matched, match_rate, len(censored),
            )

    # Assemble final DataFrame
    if all_rows:
        cycle_df = pd.DataFrame(all_rows).reset_index(drop=True)
        # Ensure correct dtypes for targets
        cycle_df[TARGET_PRIMARY]  = cycle_df[TARGET_PRIMARY].astype(float)
        cycle_df[TARGET_BINARY]   = cycle_df[TARGET_BINARY].astype(int)
        cycle_df[TARGET_FILLS]    = cycle_df[TARGET_FILLS].astype(int)
        cycle_df[TARGET_DURATION] = cycle_df[TARGET_DURATION].astype(int)
    else:
        cycle_df = pd.DataFrame()

    total_matched   = sum(r.matched     for r in reports)
    total_censored  = sum(r.censored_cycles for r in reports)
    total_unmatched = sum(r.unmatched   for r in reports)

    logger.info(
        "dca_extractor: DONE — %d matched cycles, %d censored excluded, %d unmatched",
        total_matched, total_censored, total_unmatched,
    )

    return AlignmentResult(
        cycle_df=cycle_df,
        reports=reports,
        pairs_found=pairs_found,
        total_matched=total_matched,
        total_censored=total_censored,
        total_unmatched=total_unmatched,
        feature_family=list(ENTRY_FEATURE_FAMILY),
    )  # claude code changed: new — main DCA cycle extractor entry point


def print_alignment_report(result: AlignmentResult) -> None:
    """Print a human-readable alignment report to stdout."""
    print("\n" + "=" * 70)
    print("DCA CYCLE EXTRACTOR — ALIGNMENT REPORT")
    print("=" * 70)
    print(f"Pairs processed : {len(result.reports)}")
    print(f"Pairs with data : {len(result.pairs_found)}")
    print(f"Total matched   : {result.total_matched} cycles")
    print(f"Total censored  : {result.total_censored} (excluded — DATA_END_STILL_OPEN)")
    print(f"Total unmatched : {result.total_unmatched} (decision candle not in obs CSV)")
    print()
    print(f"{'Pair':<12} {'Closed':>7} {'Matched':>8} {'Match%':>7} {'Cens':>5} {'ObsRows':>8}")
    print("-" * 55)
    for r in result.reports:
        print(f"{r.pair:<12} {r.closed_cycles:>7} {r.matched:>8} "
              f"{r.match_rate_pct:>6.1f}% {r.censored_cycles:>5} {r.obs_row_count:>8}")
    print()
    if not result.cycle_df.empty:
        print("Feature null rates (%) across matched cycles:")
        feat_df = result.cycle_df[result.feature_family]
        null_pcts = (feat_df.isna().mean() * 100).round(1)
        for feat, pct in null_pcts.items():
            flag = " ← WARN" if pct > 10 else ""
            print(f"  {feat:<28} {pct:>5.1f}%{flag}")
    print("=" * 70)
