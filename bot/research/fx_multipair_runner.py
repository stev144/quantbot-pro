"""
bot/research/fx_multipair_runner.py
Quick-cycle multi-pair DCA runner — QC-1 protocol.

Runs the DCA engine concurrently (via independent simulations) across 5
diversified FX pairs with tighter parameters to achieve ~2 completed
trades/day across the portfolio.

QC-1 protocol (pre-registered 2026-10-18, before any simulation results seen):
  max_cycle_candles   = 72       (3-day time exit vs 30-day in A3)
  profit_activation   = 0.5%     (0.5% from WAP vs 2.5% in A3)
  trailing_pct        = 0.2%     (trailing pull-back vs 0.5% in A3)
  mae_horizon         = 6h       (6h MAE for ladder spacing vs 24h in A3)
  initial_usdt        = 6.0 per cycle per pair (same as A3)

Pair portfolio (diversified — only 1 USD pair):
  EUR/USD  — most liquid major (USD anchor)
  EUR/JPY  — cross; ECB/BoJ; no USD
  GBP/JPY  — cross; BoE/BoJ; high volatility; no USD
  AUD/CAD  — cross; commodity; RBA/BoC; no USD/EUR/GBP/JPY
  NZD/CHF  — cross; RBNZ/SNB; risk/safe-haven; no USD/EUR/GBP/JPY/AUD/CAD

NOTE: Cross pairs use placeholder specs (cost_complete=False) — MT5 cost
collection for these pairs is pending Stage 2B. EUR/USD uses MT5-verified
costs (spread=0, commission=0 on the ICFunded account).

Usage:
    python bot/research/fx_multipair_runner.py [--direction BOTH|LONG|SHORT]
"""
# claude code changed: new file — 2026-10-18; QC-1 quick-cycle multi-pair DCA runner

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# ── QC-1 protocol constants (pre-registered 2026-10-18) ───────────────────────
QC1_MAX_CYCLE_CANDLES = 72      # claude code changed: 3-day time exit (was 720 in A3)
QC1_PROFIT_ACTIVATION = 0.005   # claude code changed: 0.5% from WAP (was 0.025 = 2.5% in A3)
QC1_TRAILING_PCT = 0.002        # claude code changed: 0.2% trailing (was 0.005 = 0.5% in A3)
QC1_MAE_HORIZON = 6             # claude code changed: 6h MAE for ladder (was 24h in A3)
QC1_INITIAL_USDT = 6.0          # claude code changed: same as A3
QC1_PROTOCOL_ID = "FX-QC-PROTOCOL-1"  # claude code changed: new protocol ID

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_pair_df(csv_file: str) -> pd.DataFrame:
    """Load a 1h FX CSV from data/forex/ and return with DatetimeIndex."""  # claude code changed: new helper
    csv_path = _PROJECT_ROOT / "data" / "forex" / csv_file
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    return df


def run_multipair_fold(
    pair: str,
    csv_file: str,
    pair_spec,
    pip_size: float,
    direction: str,
    fold,
    fold_num: int,
    verbose: bool = False,
) -> Dict:
    """
    Run one fold for one pair under QC-1 parameters.
    Returns the fold result dict from run_fx_fold.
    """  # claude code changed: new — wraps run_fx_fold with QC-1 parameters
    from bot.research.fx_fold_runner import run_fx_fold  # claude code changed: deferred import

    return run_fx_fold(
        train_df=fold.train,
        test_df=fold.test,
        pair=pair,
        direction=direction,
        pip_size=pip_size,
        pair_spec=pair_spec,
        initial_usdt=QC1_INITIAL_USDT,
        profit_activation_pct=QC1_PROFIT_ACTIVATION,
        trailing_pct=QC1_TRAILING_PCT,
        fold_num=fold_num,
        max_cycle_candles=QC1_MAX_CYCLE_CANDLES,
        apply_v6_gates=False,   # QC-1 does not use carry/ADX gates
        mae_horizon=QC1_MAE_HORIZON,
    )


def _daily_trade_rate(fold_results: List[Dict], pip_size: float) -> float:
    """
    Compute average completed trades per calendar day across all folds.
    Counts only closed cycles (excludes DATA_END_STILL_OPEN).
    """  # claude code changed: new helper
    total_closed = 0
    total_candles = 0
    for r in fold_results:
        closed = [t for t in r["trades"] if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
        total_closed += len(closed)
        total_candles += r["test_rows"]
    total_days = total_candles / 24.0   # 1h candles -> days
    return total_closed / total_days if total_days > 0 else 0.0


def run_portfolio(
    direction: str = "LONG",
    verbose: bool = True,
) -> Dict[str, List[Dict]]:
    """
    Run all 5 QC-1 pairs, all 5 folds, for the given direction.
    Returns {pair: [fold1_result, fold2_result, ...], ...}.
    """  # claude code changed: new — main portfolio runner
    from bot.research.fx_fold_splitter import make_fx_splits
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES

    if verbose:
        print(f"\n{'='*72}")
        print(f"  QC-1 MULTI-PAIR QUICK-CYCLE DCA — {direction}")
        print(f"  Protocol: {QC1_PROTOCOL_ID} (pre-registered 2026-10-18)")
        print(f"  time_exit={QC1_MAX_CYCLE_CANDLES}h  profit_act={QC1_PROFIT_ACTIVATION:.1%}  "
              f"trailing={QC1_TRAILING_PCT:.1%}  ladder_horizon={QC1_MAE_HORIZON}h")
        print(f"{'='*72}")

    all_results: Dict[str, List[Dict]] = {}

    for pair, pair_spec in QC_PORTFOLIO_SPECS.items():
        csv_file = QC_PORTFOLIO_CSV_FILES[pair]
        pip_size = pair_spec.pip_size

        if verbose:
            print(f"\n  Loading {pair} from {csv_file} ...")

        df = _load_pair_df(csv_file)
        splits = make_fx_splits(df)

        if verbose:
            print(f"  Calibration: {len(splits.calibration):,} rows  |  "
                  f"Holdout: {len(splits.holdout):,} rows [SEALED]")

        fold_results = []
        for fold in splits.folds:
            t0 = time.time()
            if verbose:
                print(f"    Fold {fold.fold_num}  train={fold.train_size:,}  "
                      f"test={fold.test_size:,} ...", end="", flush=True)
            result = run_multipair_fold(
                pair=pair,
                csv_file=csv_file,
                pair_spec=pair_spec,
                pip_size=pip_size,
                direction=direction,
                fold=fold,
                fold_num=fold.fold_num,
            )
            fold_results.append(result)
            s = result["summary"]
            if verbose:
                pnl = s.get("total_profit_usdt", 0.0)
                print(f"  trades={s['total_trades']}  P&L={pnl:+.4f}  [{time.time()-t0:.1f}s]")

        all_results[pair] = fold_results

    return all_results


def print_portfolio_summary(
    all_results: Dict[str, List[Dict]],
    direction: str,
) -> None:
    """Print per-pair and aggregate portfolio summary."""  # claude code changed: new — portfolio-level reporting
    print(f"\n{'='*72}")
    print(f"  PORTFOLIO SUMMARY — {direction}  |  {QC1_PROTOCOL_ID}")
    print(f"  Parameters: time_exit={QC1_MAX_CYCLE_CANDLES}h  "
          f"profit_act={QC1_PROFIT_ACTIVATION:.1%}  trailing={QC1_TRAILING_PCT:.1%}  "
          f"ladder_H={QC1_MAE_HORIZON}h")
    print(f"{'='*72}")
    print(f"  {'Pair':<12}  {'Trades':>7}  {'P&L(USD)':>10}  {'Win%':>6}  "
          f"{'AvgHold':>8}  {'Trades/Day':>10}  {'CostOK':>7}")
    print(f"  {'-'*12}  {'-'*7}  {'-'*10}  {'-'*6}  {'-'*8}  {'-'*10}  {'-'*7}")

    total_trades = 0
    total_pnl = 0.0
    total_candles = 0
    all_wins = 0
    all_closed_count = 0

    for pair, fold_results in all_results.items():
        pair_trades = sum(r["summary"]["total_trades"] for r in fold_results)
        pair_pnl = sum(r["summary"].get("total_profit_usdt", 0.0) for r in fold_results)
        pair_candles = sum(r["test_rows"] for r in fold_results)
        pair_days = pair_candles / 24.0

        # win rate across all closed trades
        all_profits = []
        avg_hold_vals = []
        for r in fold_results:
            closed = [t for t in r["trades"] if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
            all_profits.extend(t["profit_usdt"] for t in closed)
            if closed:
                avg_hold_vals.extend(t["candles_held"] for t in closed)

        wins = sum(1 for p in all_profits if p > 0)
        win_rate = wins / len(all_profits) if all_profits else float("nan")
        avg_hold = sum(avg_hold_vals) / len(avg_hold_vals) if avg_hold_vals else float("nan")
        tpd = pair_trades / pair_days if pair_days > 0 else 0.0
        cost_ok = all(r["summary"].get("cost_complete", False) for r in fold_results)
        cost_label = "YES" if cost_ok else "NO"

        print(f"  {pair:<12}  {pair_trades:>7}  {pair_pnl:>+10.4f}  "
              f"{win_rate:>6.1%}  {avg_hold:>7.0f}h  {tpd:>10.3f}  {cost_label:>7}")

        total_trades += pair_trades
        total_pnl += pair_pnl
        total_candles += pair_candles
        all_wins += wins
        all_closed_count += len(all_profits)

    # calendar days = total_candles / num_pairs / 24  (all pairs cover same time window)
    num_pairs = len(all_results)
    calendar_days = (total_candles / num_pairs) / 24.0 if num_pairs > 0 else 0.0
    total_tpd = total_trades / calendar_days if calendar_days > 0 else 0.0  # claude code changed: fix — was dividing by 5× calendar days
    overall_win = all_wins / all_closed_count if all_closed_count > 0 else float("nan")

    print(f"  {'-'*12}  {'-'*7}  {'-'*10}  {'-'*6}  {'-'*8}  {'-'*10}  {'-'*7}")
    print(f"  {'PORTFOLIO':<12}  {total_trades:>7}  {total_pnl:>+10.4f}  "
          f"{overall_win:>6.1%}  {'':>8}  {total_tpd:>10.3f}  {'':>7}")

    # per-fold breakdown
    print(f"\n  -- Per-fold trade count across all pairs --")
    fold_nums = sorted({r["fold_num"] for frs in all_results.values() for r in frs})
    for fn in fold_nums:
        fold_trades = sum(
            next((r["summary"]["total_trades"] for r in frs if r["fold_num"] == fn), 0)
            for frs in all_results.values()
        )
        fold_rows = next(
            (r["test_rows"] for frs in all_results.values() for r in frs if r["fold_num"] == fn),
            0,
        )
        fold_days = fold_rows / 24.0
        fold_tpd = fold_trades / fold_days if fold_days > 0 else 0.0
        print(f"    Fold {fn}: {fold_trades} trades across 5 pairs over {fold_days:.0f} days "
              f"= {fold_tpd:.3f} trades/day")

    print(f"\n  TARGET: ~2.0 trades/day  |  RESULT: {total_tpd:.3f} trades/day")
    print(f"  [Cost-complete: EUR/USD only (MT5-verified); other 4 pairs PLACEHOLDER]")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("QUANTBOT_SECRET_KEY", "dummy-key-for-research")
    sys.path.insert(0, str(_PROJECT_ROOT))

    _parser = argparse.ArgumentParser(description="QC-1 multi-pair quick-cycle DCA runner")
    _parser.add_argument(
        "--direction",
        default="BOTH",
        choices=["BOTH", "LONG", "SHORT"],
        help="Direction(s) to simulate (default: BOTH)",
    )
    _args = _parser.parse_args()

    _directions = ["LONG", "SHORT"] if _args.direction == "BOTH" else [_args.direction]

    for _dir in _directions:
        _results = run_portfolio(direction=_dir, verbose=True)
        print_portfolio_summary(_results, _dir)
