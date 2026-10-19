"""
bot/fx_intraday_runner.py
Walk-forward feasibility run: H2  -  MeanReversionStrategy, 15m, EUR/USD.

Governance references:
  D-7 confirmed 2026-10-18: 5 folds, IS=5973 bars, OOS=2986 bars, embargo=67.
  D-4 confirmed 2026-10-18: 5-test Bonferroni family, alpha=0.010.
  D-9 registered  2026-10-18: MeanReversionStrategy default parameters (see governance log).

Usage:
  python bot/fx_intraday_runner.py                  # EUR/USD 15m, MR strategy
  python bot/fx_intraday_runner.py --pair EUR/USD   # explicit pair
  python bot/fx_intraday_runner.py --no-permtest    # skip permutation test (fast)
  python bot/fx_intraday_runner.py --save           # save OOS trades to research_data/

Cost model: spread 0.5 pips round-trip per trade (conservative; snapshot was 0.0 pips).
P&L: (exit - entry) x units, in USD (EUR/USD quote = USD; direct, no conversion needed).
USD/CHF note: P&L would be in CHF without conversion  -  this script is EUR/USD only.
"""
# claude code changed: new file  -  intraday FX walk-forward runner; H2 MR 15m EUR/USD;
# D-7/D-4/D-9 confirmed 2026-10-18; pre-registered parameters; no parameter fitting.

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QUANTBOT_SECRET_KEY", "dummy-key-for-offline-script")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

import numpy as np
import pandas as pd

from bot.engines.regime_detector import RegimeDetector
from bot.strategies.mean_reversion_strategy import MeanReversionStrategy
from bot.strategies.pullback_strategy import PullbackStrategy  # claude code changed: D-5 pullback family
from bot.risk.position_sizer import PositionSizer
from bot.research.fx_permutation_test import block_shuffle_returns  # M3 null model
from bot.research.permutation_stats import compute_permutation_verdict
from bot.research.fx_holdout_guard import V7_HOLDOUT_START  # claude code changed: seal check

logger = logging.getLogger(__name__)

# â"€â"€ D-7 locked fold structure â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
N_FOLDS        = 5
IS_BARS        = 5_973   # 88 trading days x ~67.9 bars/day
OOS_BARS       = 2_986   # 44 trading days x ~67.9 bars/day
EMBARGO_BARS   = 67      # 1 trading day

# â"€â"€ D-9 registered parameters (must not change without new pre-registration) â"€â"€
SPREAD_PIPS     = 0.5    # round-trip cost; conservative; snapshot was 0.0
PIP_SIZE        = 0.0001 # EUR/USD pip size
INITIAL_BALANCE = 1_000.0
MAX_HOLD_BARS   = 32     # 8h at 15m (approved risk limits)
RISK_PCT        = 0.01   # 1% per trade
RR_RATIO        = 1.5    # registered rr_ratio; used to rebase TP from entry price (D-9 amendment 2026-10-18)

# â"€â"€ D-4 Bonferroni alpha â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
BONFERRONI_ALPHA = 0.010   # 5-test family

# â"€â"€ Permutation test spec (D-9) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
N_PERMUTATIONS = 499
BLOCK_SIZE     = 48        # 48 x 15m = 12h blocks


# â"€â"€ Data loading â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def load_dev_data(pair: str = "EUR/USD", interval: str = "15m") -> pd.DataFrame:
    """Load development-period data. All rows are pre-holdout (fetched with V7 seal)."""  # claude code changed: new
    filename = pair.replace("/", "_") + f"_{interval}.csv"
    path = Path("data/forex") / filename
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    # Verify seal  -  all rows must be before V7_HOLDOUT_START
    if (df["timestamp"] >= V7_HOLDOUT_START).any():
        n_bad = int((df["timestamp"] >= V7_HOLDOUT_START).sum())
        raise ValueError(f"D-8 seal violation: {n_bad} rows at/after holdout boundary in {path.name}")
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# â"€â"€ Fold splitter â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def make_folds(n_bars: int) -> List[Dict]:
    """Build fold boundaries per D-7. Returns list of dicts with is/oos slices."""  # claude code changed: new  -  D-7 locked structure
    folds = []
    for k in range(N_FOLDS):
        offset = k * (IS_BARS + EMBARGO_BARS + OOS_BARS)
        is_start  = offset
        is_end    = offset + IS_BARS
        oos_start = is_end + EMBARGO_BARS
        oos_end   = oos_start + OOS_BARS
        if oos_end > n_bars:
            logger.warning(f"Fold {k+1}: OOS end ({oos_end}) exceeds data length ({n_bars})  -  truncating")
            oos_end = n_bars
        folds.append({
            "fold":      k + 1,
            "is_start":  is_start,
            "is_end":    is_end,
            "oos_start": oos_start,
            "oos_end":   oos_end,
        })
    return folds


# â"€â"€ Trade simulator â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def simulate_on_window(
    df: pd.DataFrame,
    oos_start: int,
    oos_end: int,
    balance: float = INITIAL_BALANCE,
    strategy=None,
) -> List[Dict]:
    """Run strategy on df[oos_start:oos_end]. Returns trades that open in OOS window."""  # claude code changed: strategy injected; supports MR and PullbackStrategy (D-5)
    rd = RegimeDetector()
    if strategy is None:
        strategy = MeanReversionStrategy(
            rsi_oversold=35, rsi_overbought=65,
            atr_multiplier=1.0, bb_width_max=6.0,
            min_candles=50, rr_ratio=1.5,
        )
    ps = PositionSizer(risk_pct=RISK_PCT)

    # Round-trip spread cost per unit (applied at entry + exit each = 0.5Ã--SPREAD_PIPS)
    half_spread = (SPREAD_PIPS / 2) * PIP_SIZE  # price units per side

    trades = []
    in_position = False
    pos: Dict = {}

    # Warmup window: up to 200 bars; at minimum, need min_candles for the strategy
    WARMUP = 200

    for i in range(oos_start, oos_end):
        win_start = max(0, i - WARMUP)
        window = df.iloc[win_start : i + 1]

        if not in_position:
            # Generate signal on completed bar i
            regime = rd.detect(window)
            signal = strategy.evaluate(window, regime)

            if signal["signal"] in ("BUY", "SELL") and i + 1 < len(df):
                # Entry at bar i+1's open (completed-bar discipline)
                entry_bar  = i + 1
                entry_open = float(df.iloc[entry_bar]["open"])
                side       = signal["signal"]

                # D-9 amendment 2026-10-18: rebase SL/TP from actual entry price.
                # strategy computes sl/tp from signal["entry"] (signal-bar close);
                # actual entry is next-bar open. Preserve ATR distance, shift anchor.
                sl_distance = abs(float(signal["entry"]) - float(signal["sl"]))
                rr = getattr(strategy, "rr_ratio", RR_RATIO)  # strategy-specific R:R  # claude code changed: use strategy.rr_ratio so pullback (2.0) vs MR (1.5) are correct
                if side == "BUY":
                    entry_price = entry_open + half_spread
                    sl = entry_price - sl_distance
                    tp = entry_price + sl_distance * rr
                else:
                    entry_price = entry_open - half_spread
                    sl = entry_price + sl_distance
                    tp = entry_price - sl_distance * rr

                units = ps.calculate(balance, entry_price, sl)
                if units == 0:
                    continue

                pos = {
                    "side":         side,
                    "entry_bar":    entry_bar,
                    "entry_time":   df.iloc[entry_bar]["timestamp"],
                    "entry_price":  entry_price,
                    "sl":           sl,
                    "tp":           tp,
                    "units":        units,
                }
                in_position = True

        else:
            # Check exit on bar i (may be in OOS or just past it  -  close out cleanly)
            candle    = df.iloc[i]
            side      = pos["side"]
            bars_held = i - pos["entry_bar"]

            exit_price  = None
            exit_reason = None

            if side == "BUY":
                if float(candle["low"]) <= pos["sl"]:
                    exit_price  = pos["sl"]
                    exit_reason = "SL"
                elif float(candle["high"]) >= pos["tp"]:
                    exit_price  = pos["tp"]
                    exit_reason = "TP"
            else:  # SELL
                if float(candle["high"]) >= pos["sl"]:
                    exit_price  = pos["sl"]
                    exit_reason = "SL"
                elif float(candle["low"]) <= pos["tp"]:
                    exit_price  = pos["tp"]
                    exit_reason = "TP"

            if exit_price is None and bars_held >= MAX_HOLD_BARS:
                exit_price  = float(candle["close"])
                exit_reason = "TIMEOUT"

            if exit_price is not None:
                # P&L gross (USD: EUR/USD quote currency is USD)
                if side == "BUY":
                    pnl_gross = (exit_price - pos["entry_price"]) * pos["units"]
                else:
                    pnl_gross = (pos["entry_price"] - exit_price) * pos["units"]

                # Spread cost at exit
                exit_cost = half_spread * pos["units"]
                pnl_net   = pnl_gross - exit_cost

                balance += pnl_net

                trades.append({
                    "fold_oos_start": oos_start,
                    "entry_time":    pos["entry_time"],
                    "exit_time":     candle["timestamp"],
                    "side":          side,
                    "entry_price":   round(pos["entry_price"], 5),
                    "exit_price":    round(exit_price, 5),
                    "sl":            round(pos["sl"], 5),
                    "tp":            round(pos["tp"], 5),
                    "units":         pos["units"],
                    "pnl_gross":     round(pnl_gross, 4),
                    "pnl_net":       round(pnl_net, 4),
                    "exit_reason":   exit_reason,
                })
                in_position = False
                pos = {}

    return trades


# â"€â"€ Walk-forward â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def run_walk_forward(df: pd.DataFrame, strategy=None) -> Tuple[List[Dict], List[Dict]]:
    """Run 5-fold OOS walk-forward. Strategy instance injected by caller."""  # claude code changed: strategy param
    folds     = make_folds(len(df))
    all_trades: List[Dict] = []
    fold_summaries: List[Dict] = []

    for fold in folds:
        fold_num = fold["fold"]
        print(f"\n-- Fold {fold_num}/5 --------------------------------------------------")
        print(f"   IS:  bars {fold['is_start']:,} - {fold['is_end']:,}")
        print(f"   Embargo: {EMBARGO_BARS} bars")
        print(f"   OOS: bars {fold['oos_start']:,} - {fold['oos_end']:,}")

        # OOS dates
        oos_df = df.iloc[fold["oos_start"]:fold["oos_end"]]
        if len(oos_df) == 0:
            print(f"   SKIP - empty OOS slice")
            continue
        print(f"   OOS dates: {oos_df.iloc[0]['timestamp']} -> {oos_df.iloc[-1]['timestamp']}")

        trades = simulate_on_window(
            df,
            oos_start=fold["oos_start"],
            oos_end=fold["oos_end"],
            strategy=strategy,
        )

        n  = len(trades)
        if n == 0:
            win_rate = float("nan")
            mean_pnl = float("nan")
            total    = 0.0
        else:
            winners  = sum(1 for t in trades if t["pnl_net"] > 0)
            win_rate = winners / n
            mean_pnl = sum(t["pnl_net"] for t in trades) / n
            total    = sum(t["pnl_net"] for t in trades)

        print(f"   Trades:   {n}")
        print(f"   Win rate: {win_rate:.1%}" if n > 0 else "   Win rate: n/a")
        print(f"   Mean net P&L: ${mean_pnl:.2f}" if n > 0 else "   Mean net P&L: n/a")
        print(f"   Total OOS P&L: ${total:.2f}")

        for t in trades:
            t["fold"] = fold_num
        all_trades.extend(trades)

        fold_summaries.append({
            "fold":      fold_num,
            "trades":    n,
            "win_rate":  win_rate,
            "mean_pnl":  mean_pnl,
            "total_pnl": total,
            "oos_start_bar": fold["oos_start"],
            "oos_end_bar":   fold["oos_end"],
        })

    return all_trades, fold_summaries


# â"€â"€ Permutation test â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def run_permutation_test(
    df: pd.DataFrame,
    real_mean_pnl: float,
    real_total_pnl: float,
    n_permutations: int = N_PERMUTATIONS,
    strategy=None,
) -> Dict:
    """M3 block-shuffle permutation test. Strategy injected by caller."""  # claude code changed: strategy param
    rng = np.random.default_rng(seed=42)
    shuffled_results: List[Dict] = []

    folds = make_folds(len(df))

    print(f"\nPermutation test: N={n_permutations}, block_size={BLOCK_SIZE} bars...")

    for perm_i in range(n_permutations):
        if perm_i % 50 == 0:
            print(f"  Permutation {perm_i}/{n_permutations}...", flush=True)

        shuffled_df = block_shuffle_returns(df, block_size=BLOCK_SIZE, rng=rng)

        perm_trades: List[Dict] = []
        for fold in folds:
            t = simulate_on_window(
                shuffled_df,
                oos_start=fold["oos_start"],
                oos_end=fold["oos_end"],
                strategy=strategy,
            )
            perm_trades.extend(t)

        n = len(perm_trades)
        perm_mean_pnl  = sum(t["pnl_net"] for t in perm_trades) / n if n > 0 else 0.0
        perm_total_pnl = sum(t["pnl_net"] for t in perm_trades) if n > 0 else 0.0

        shuffled_results.append({
            "mean_pnl":  perm_mean_pnl,
            "total_pnl": perm_total_pnl,
        })

    real = {"mean_pnl": real_mean_pnl, "total_pnl": real_total_pnl}
    verdict = compute_permutation_verdict(
        real=real,
        shuffled=shuffled_results,
        metrics=["mean_pnl", "total_pnl"],
        significance_percentile=1.0 - BONFERRONI_ALPHA,  # 0.990 for alpha=0.010
    )
    return verdict


# â"€â"€ Results printer â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def print_results(all_trades, fold_summaries, verdict, pair, interval="?", strategy_label="Strategy"):  # claude code changed: strategy_label param added 2026-10-18
    n_total    = len(all_trades)
    if n_total == 0:
        print("\nâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")
        print("RESULT: 0 OOS trades generated. Strategy did not fire.")
        return

    winners    = sum(1 for t in all_trades if t["pnl_net"] > 0)
    win_rate   = winners / n_total
    mean_pnl   = sum(t["pnl_net"] for t in all_trades) / n_total
    total_pnl  = sum(t["pnl_net"] for t in all_trades)
    max_win    = max(t["pnl_net"] for t in all_trades)
    max_loss   = min(t["pnl_net"] for t in all_trades)

    exit_reasons = {}
    for t in all_trades:
        exit_reasons[t["exit_reason"]] = exit_reasons.get(t["exit_reason"], 0) + 1

    print("\nâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")
    print(f"{strategy_label} {interval} {pair} 5-fold OOS walk-forward")  # claude code changed: uses strategy_label
    print(f"Pre-registered parameters (D-9)  -  no parameter fitting")
    print("â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")
    print(f"\nOVERALL OOS RESULTS ({N_FOLDS} folds combined):")
    print(f"  Trades:          {n_total}")
    print(f"  Win rate:        {win_rate:.1%}")
    print(f"  Mean net P&L:    ${mean_pnl:.2f} per trade")
    print(f"  Total net P&L:   ${total_pnl:.2f}")
    print(f"  Best trade:      ${max_win:.2f}")
    print(f"  Worst trade:     ${max_loss:.2f}")
    print(f"  Exit reasons:    {exit_reasons}")
    print(f"\nPER-FOLD BREAKDOWN:")
    print(f"  {'Fold':>4}  {'Trades':>6}  {'Win%':>6}  {'Mean$':>8}  {'Total$':>9}")
    for s in fold_summaries:
        wr = f"{s['win_rate']:.1%}" if s["trades"] > 0 else "n/a"
        mn = f"${s['mean_pnl']:.2f}" if s["trades"] > 0 else "n/a"
        print(f"  {s['fold']:>4}  {s['trades']:>6}  {wr:>6}  {mn:>8}  ${s['total_pnl']:>8.2f}")

    if verdict:
        print(f"\nPERMUTATION TEST (M3 block-shuffle, N={N_PERMUTATIONS}):")
        print(f"  Block size: {BLOCK_SIZE} bars (12h)  |  alpha = {BONFERRONI_ALPHA} (D-4 Bonferroni)")
        for metric in ("mean_pnl", "total_pnl"):
            if metric in verdict.get("metrics", {}):
                m = verdict["metrics"][metric]
                sig = "*** SIGNIFICANT" if m.get("significant") else "not significant"
                print(f"  {metric}: p={m.get('p_value', '?'):.4f}  "
                      f"Z={m.get('z_score', '?'):.3f}  percentile={m.get('percentile', '?'):.3f}  {sig}")
        edge_real = verdict.get("edge_appears_real", False)
        print(f"\n  edge_appears_real = {edge_real}")
        if edge_real:
            print(f"  *** H2 PRIMARY STATISTIC (mean_pnl) SIGNIFICANT at alpha={BONFERRONI_ALPHA} ***")
            print(f"  NOTE: This is development-period evidence only. Holdout sealed.")
        else:
            print(f"  H2 not significant at alpha={BONFERRONI_ALPHA}.")
    print("â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•")


# â"€â"€ Entry point â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def main():  # claude code changed: --strategy flag added 2026-10-18 for H4 pullback run
    parser = argparse.ArgumentParser(description="Intraday FX walk-forward runner (D-4 family)")
    parser.add_argument("--pair", default="EUR/USD", help="Protocol pair (default: EUR/USD)")
    parser.add_argument("--interval", default="15m", help="Timeframe (default: 15m)")
    parser.add_argument("--strategy", default="mr", choices=["mr", "pullback"],
                        help="Strategy to run: 'mr' = MeanReversionStrategy (default), 'pullback' = PullbackStrategy")
    parser.add_argument("--no-permtest", action="store_true", help="Skip permutation test")
    parser.add_argument("--save", action="store_true", help="Save OOS trades to research_data/")
    parser.add_argument("--is-bars", type=int, default=None, help="Override IS bars (default: D-7 constant)")
    parser.add_argument("--oos-bars", type=int, default=None, help="Override OOS bars")
    parser.add_argument("--embargo-bars", type=int, default=None, help="Override embargo bars")
    parser.add_argument("--max-hold", type=int, default=None, help="Override MAX_HOLD_BARS")
    args = parser.parse_args()

    # Apply fold-structure overrides
    global IS_BARS, OOS_BARS, EMBARGO_BARS, MAX_HOLD_BARS
    if args.is_bars is not None:
        IS_BARS = args.is_bars
    if args.oos_bars is not None:
        OOS_BARS = args.oos_bars
    if args.embargo_bars is not None:
        EMBARGO_BARS = args.embargo_bars
    if args.max_hold is not None:
        MAX_HOLD_BARS = args.max_hold

    # Construct strategy instance from pre-registered defaults (D-5)
    if args.strategy == "pullback":
        strategy = PullbackStrategy()
        strategy_label = "PullbackStrategy"
    else:
        strategy = MeanReversionStrategy()
        strategy_label = "MeanReversionStrategy"

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s | %(name)s | %(message)s")

    print(f"\n{'='*63}")
    print(f"INTRADAY FX WALK-FORWARD")
    print(f"Strategy  : {strategy_label} (pre-registered defaults)")
    print(f"Pair      : {args.pair}  |  Interval: {args.interval}")
    print(f"Folds     : {N_FOLDS}  |  IS={IS_BARS} bars  OOS={OOS_BARS} bars  embargo={EMBARGO_BARS}")
    print(f"D-4 alpha : {BONFERRONI_ALPHA} (5-test Bonferroni)")
    print(f"D-9 spread: {SPREAD_PIPS} pips round-trip")
    print(f"{'='*63}")

    df = load_dev_data(pair=args.pair, interval=args.interval)
    print(f"\nLoaded: {len(df):,} bars  ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")

    total_needed = N_FOLDS * (IS_BARS + EMBARGO_BARS + OOS_BARS)
    if len(df) < total_needed:
        print(f"WARNING: only {len(df):,} bars; need {total_needed:,} for {N_FOLDS} complete folds")

    all_trades, fold_summaries = run_walk_forward(df, strategy=strategy)

    n_total   = len(all_trades)
    mean_pnl  = sum(t["pnl_net"] for t in all_trades) / n_total if n_total > 0 else 0.0
    total_pnl = sum(t["pnl_net"] for t in all_trades)

    verdict = None
    if not args.no_permtest and n_total > 0:
        verdict = run_permutation_test(df, real_mean_pnl=mean_pnl, real_total_pnl=total_pnl,
                                       strategy=strategy)

    print_results(all_trades, fold_summaries, verdict, args.pair, interval=args.interval,
                  strategy_label=strategy_label)

    if args.save and all_trades:
        out_dir = Path("research_data")
        out_dir.mkdir(exist_ok=True)
        strat_tag = "Pullback" if args.strategy == "pullback" else "MR"
        hyp_tag = f"{strat_tag}_{args.interval}_{args.pair.replace('/', '_')}"
        out_path = out_dir / f"{hyp_tag}_oos_trades.csv"
        pd.DataFrame(all_trades).to_csv(out_path, index=False)
        print(f"\nOOS trades saved -> {out_path}")


if __name__ == "__main__":
    main()
