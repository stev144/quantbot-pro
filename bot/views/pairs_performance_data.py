# ============================================================
# bot/views/pairs_performance_data.py
# claude code changed: new file — data layer for the Pairs Performance
# page. Same rule as research_lab_data.py/feature_intelligence_data.py:
# every function reads bot/research/entry_exit_engine.py's real persisted
# output from research_data/, never invokes research code live, and
# returns an explicit {"available": False, "reason": ...} marker instead
# of raising when a file is missing.
#
# NAMING SCHEME CAVEAT: entry_exit_engine.py's _save_outputs() names the
# 4 core files with a SHORT prefix built from the pair's own symbols
# (e.g. "AVAX_ATOM" from AVAX_USDT/ATOM_USDT) — see
# bot/research/entry_exit_engine.py:2085-2087. walk_forward_engine.py and
# permutation_test_engine.py instead use the LONG
# "<SYMBOL_A>_USDT_<SYMBOL_B>_USDT" form for their own directory names.
# _long_pair_name() below reconstructs one from the other. This only
# works if neither ticker itself contains an underscore — true for every
# pair in this repo today; a future pair with an underscore in its ticker
# would need this reworked.
# ============================================================

import glob
import os

import pandas as pd

from bot.views.research_lab_data import RESEARCH_DATA_DIR

PERMUTATION_DIR = os.path.join(RESEARCH_DATA_DIR, "permutation_test")
WALK_FORWARD_LEADERBOARD = os.path.join(RESEARCH_DATA_DIR, "walk_forward", "walk_forward_leaderboard.csv")


def discover_pairs():
    """Every pair with a real entry_exit_engine.py equity curve on disk."""
    files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_equity_curve.csv"))
    return sorted(os.path.basename(f).replace("_equity_curve.csv", "") for f in files)


def _long_pair_name(prefix: str) -> str:
    """"AVAX_ATOM" -> "AVAX_USDT_ATOM_USDT" — see module docstring."""
    parts = prefix.split("_")
    if len(parts) != 2:
        return prefix
    return f"{parts[0]}_USDT_{parts[1]}_USDT"


def _read_equity_curve(prefix: str):
    path = os.path.join(RESEARCH_DATA_DIR, f"{prefix}_equity_curve.csv")
    if not os.path.exists(path):
        return [], []
    df = pd.read_csv(path)
    equity_curve = df["cumulative_usdt"].round(2).tolist()
    # claude code changed: entry_exit_engine.py's drawdown_pct is a signed
    # fraction (negative or zero — see _build_equity_curve()); dashboard.html's
    # drawdown_curve convention is a positive percentage magnitude.
    drawdown_curve = (df["drawdown_pct"].abs() * 100).round(2).tolist()
    return equity_curve, drawdown_curve


def _read_trade_log(prefix: str):
    path = os.path.join(RESEARCH_DATA_DIR, f"{prefix}_trade_log.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    display_cols = [
        "trade_id", "entry_timestamp", "exit_timestamp", "direction",
        "entry_zscore", "exit_zscore", "hours_held", "exit_reason",
        "net_pnl_usdt", "net_pnl_pct", "is_winner",
    ]
    df = df[[c for c in display_cols if c in df.columns]]
    return df.to_dict("records")


def _read_strategy_summary(prefix: str):
    path = os.path.join(RESEARCH_DATA_DIR, f"{prefix}_strategy_summary.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    summary = {k: (None if pd.isna(v) else v) for k, v in row.items()}
    # claude code changed: win_rate/max_drawdown_pct are stored as signed
    # fractions (e.g. 0.896, -0.0065) in the CSV, not percentages — scale
    # to percent here so the template can display them directly.
    for frac_field in ("win_rate", "max_drawdown_pct"):
        if summary.get(frac_field) is not None:
            summary[frac_field] = round(summary[frac_field] * 100, 2)
    return summary


def _read_permutation_verdict(long_name: str):
    path = os.path.join(PERMUTATION_DIR, long_name, "permutation_verdict.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    return {k: (None if pd.isna(v) else v) for k, v in row.items()}


def _read_walk_forward_row(long_name: str):
    if not os.path.exists(WALK_FORWARD_LEADERBOARD):
        return {}
    df = pd.read_csv(WALK_FORWARD_LEADERBOARD)
    match = df[df["pair_name"] == long_name]
    if match.empty:
        return {}
    row = match.iloc[0].to_dict()
    return {k: (None if pd.isna(v) else v) for k, v in row.items()}


def get_pair_performance(prefix: str) -> dict:
    """
    Full performance picture for one pair, assembled from
    entry_exit_engine.py/permutation_test_engine.py/walk_forward_engine.py's
    real persisted output. Returns {"available": False, "reason": ...} if
    the pair's core equity-curve file doesn't exist.
    """
    equity_path = os.path.join(RESEARCH_DATA_DIR, f"{prefix}_equity_curve.csv")
    if not os.path.exists(equity_path):
        return {
            "available": False,
            "reason": f"No equity curve found for '{prefix}' — run entry_exit_engine.py for this pair first.",
        }

    long_name = _long_pair_name(prefix)
    equity_curve, drawdown_curve = _read_equity_curve(prefix)

    return {
        "available": True,
        "prefix": prefix,
        "long_name": long_name,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "trades": _read_trade_log(prefix),
        "summary": _read_strategy_summary(prefix),
        "permutation": _read_permutation_verdict(long_name),
        "walk_forward": _read_walk_forward_row(long_name),
    }
