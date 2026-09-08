# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/run_cross_sectional_oos.py
#
# CROSS-SECTIONAL RESEARCH — CONNECTIVE TISSUE (Milestone C of the
# statistics-infrastructure mission, mission Phase 3).
#
# claude code changed: new file. Before this, two disconnected
# cross-sectional code paths existed: cross_section_engine.py (real
# feature computation, already migrated to the live universe registry)
# and evaluate_cross_sectional_oos() in oos_validator.py (a real,
# well-built Type C evaluator with nothing feeding it). This file is
# ONLY a reshape + orchestration layer — it computes ZERO new
# statistics of its own. Feature computation is reused unchanged via
# cross_section_engine.run_cross_section_research() (renamed on import
# to compute_cross_section_features to avoid confusion with THIS file's
# own run_cross_sectional_research(), a different function).
#
# Verified compatibility (per the mission's own "do not assume a
# capability exists/fits" principle) rather than assumed:
# CrossSectionEngine.calculate_all() returns Dict[symbol -> WIDE
# per-symbol DataFrame] with columns cs_rank / cs_rank_norm / cs_zscore /
# cs_percentile / cs_momentum_3h / cs_momentum_6h / cs_reversal_signal /
# forward_return_1h (bot/research/cross_section_engine.py's own
# _merge_features_back() docstring). evaluate_cross_sectional_oos()
# needs LONG format: one row per (timestamp, asset), with a single named
# feature column and a single named forward-return column. This module's
# build_long_format_panel() is the (trivial, lossless) reshape between
# the two — a melt/stack, not a recomputation.
#
# This already covers 4 of Phase 3's 6 named cross-sectional hypotheses
# with ZERO new feature-computation code: cross-sectional return ranking
# (cs_rank/cs_percentile), cross-sectional z-scores (cs_zscore), relative
# strength (cs_rank_norm/cs_percentile), cross-sectional mean reversion
# (cs_reversal_signal). Volatility-adjusted ranking is NOT yet computed
# anywhere in this codebase — a genuine gap, left as an explicit, honest
# "not yet implemented" rather than silently substituted with one of the
# above.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from bot.research.cross_section_engine import run_cross_section_research as compute_cross_section_features
from bot.research.cross_sectional_permutation_test import run_topk_sweep
from bot.research.oos_validator import WalkForwardConfig
from bot.research_lab.data_fingerprint import fingerprint_dataset  # claude code changed: new — see run_cross_sectional_research()'s comment

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# Features cross_section_engine.py already computes and this module can
# test as-is (Phase 3 hypothesis name -> the engine's real column name).
# "volatility_adjusted_ranking" is deliberately absent — see module docstring.
AVAILABLE_FEATURES: Dict[str, str] = {
    "cross_sectional_return_ranking": "cs_rank",
    "cross_sectional_percentile_ranking": "cs_percentile",
    "cross_sectional_zscore": "cs_zscore",
    "relative_strength": "cs_rank_norm",
    "cross_sectional_mean_reversion": "cs_reversal_signal",
}

FORWARD_RETURN_COL: str = "forward_return_1h"   # cross_section_engine.py's only current label — see its own _calculate_forward_returns()

DEFAULT_VERDICT_PATH: str = "research_data/cross_sectional_family_verdict.json"


def build_long_format_panel(
    enriched_data: Dict[str, pd.DataFrame],
    feature_col: str,
    forward_return_col: str = FORWARD_RETURN_COL,
) -> pd.DataFrame:
    """
    Reshape cross_section_engine.py's Dict[symbol -> wide per-symbol
    DataFrame] into the long (timestamp, asset, feature, forward_return)
    shape evaluate_cross_sectional_oos() requires. A pure reshape — no
    row is dropped except where BOTH the feature and label are missing
    for that (timestamp, asset), and no value is recomputed.
    """
    rows: List[pd.DataFrame] = []
    for symbol, df in enriched_data.items():
        if feature_col not in df.columns or forward_return_col not in df.columns:
            logger.warning(f"  {symbol}: missing '{feature_col}' or '{forward_return_col}' — excluded from the panel")
            continue
        sub = df[[feature_col, forward_return_col]].dropna(how="all").reset_index()
        sub = sub.rename(columns={sub.columns[0]: "timestamp", feature_col: "feature", forward_return_col: "forward_return"})
        sub["asset"] = symbol
        rows.append(sub[["timestamp", "asset", "feature", "forward_return"]])

    if not rows:
        raise ValueError(f"no asset had both '{feature_col}' and '{forward_return_col}' populated — nothing to build a panel from")

    return pd.concat(rows, ignore_index=True)


def run_cross_sectional_research(
    hypothesis_name: str,
    data_dir: str = "data",
    research_data_dir: str = "research_data",
    top_k_values: Sequence[int] = (1, 3, 5, 10),
    n_permutations: int = 100,
    config: Optional[WalkForwardConfig] = None,
    long_short: bool = True,
    cost_rate: float = 0.0015,   # claude code changed: matches this project's own binance round-trip default (bot.config.execution_costs — 0.1% fee + 0.05% slippage, one side; evaluator doubles internally for round-trip)
    verdict_output_path: str = DEFAULT_VERDICT_PATH,
    checkpoint: bool = True,   # claude code changed: new — per-config checkpointing, see cross_sectional_permutation_test.py's run_topk_sweep() docstring. Real, evidenced need: an overnight run of this exact function was silently killed (machine sleep/reboot) after 2 of 4 top-K configs had already finished hours of real compute, with nothing on disk to show for it — this defaults to True so that never happens again without the caller having to remember to opt in.
    force_recompute: bool = False,   # claude code changed: new — escape hatch, see run_topk_sweep()
    rebalance_frequency: int = 24,   # claude code changed: new — real bug fix, see oos_validator.py's evaluate_cross_sectional_oos() docstring. Default 24 = daily rebalancing on this hourly panel, a defensible realistic cadence for a cross-sectional ranking strategy (vs. the previous implicit "rebalance every single hour" which produced a >10,000% cumulative-cost, Sharpe -20-to-70 artifact on the real 100-asset run). Pass 1 to reproduce the old (unrealistic) full-hourly-turnover behavior if ever needed for comparison.
) -> Dict:
    """
    Compute cross_section_engine.py's real features across the CURRENT
    universe (bot.universe_selector — never hardcoded, per the mission's
    own explicit requirement), reshape into long format, and run the
    complete Type C pipeline (evaluate_cross_sectional_oos + the top-K
    permutation sweep with FDR correction) as ONE declared hypothesis
    family.

    Parameters
    ----------
    hypothesis_name : str
        Must be a key of AVAILABLE_FEATURES — which of Phase 3's named
        cross-sectional hypotheses to test. Never a free-text column
        name, so a typo fails loudly rather than silently testing the
        wrong (or a nonexistent) feature.
    """
    if hypothesis_name not in AVAILABLE_FEATURES:
        raise ValueError(
            f"'{hypothesis_name}' is not an available cross-sectional hypothesis. "
            f"Available: {list(AVAILABLE_FEATURES.keys())}"
        )
    feature_col = AVAILABLE_FEATURES[hypothesis_name]

    logger.info("=" * 70)
    logger.info(f"CROSS-SECTIONAL RESEARCH — hypothesis: {hypothesis_name} (feature: {feature_col})")
    logger.info("=" * 70)

    enriched_data = compute_cross_section_features(data_dir=data_dir, output_dir=research_data_dir)
    panel = build_long_format_panel(enriched_data, feature_col=feature_col)
    logger.info(f"  Long-format panel: {len(panel):,} rows, {panel['asset'].nunique()} assets, {panel['timestamp'].nunique():,} timestamps")

    # claude code changed: new — dataset fingerprinting was real, tested,
    # and correctly built (bot/research_lab/data_fingerprint.py) but a
    # forensic audit found it dormant in every real run: none of the 10
    # standalone research engines called it, and this file (the actual
    # entry point for every cross-sectional experiment this session ran)
    # never supplied one either, despite oos_validator.py's evaluators
    # already accepting a data_fingerprint param end-to-end. Deliberately
    # scoped narrow — this wires the ONE real orchestrator this session
    # built and controls, not a retrofit of all 10 legacy engines (a much
    # larger, separate piece of work). DatasetIdentity's own "symbol"
    # field is single-asset-shaped; for a genuinely multi-asset panel,
    # the sorted, comma-joined list of every asset ACTUALLY included is
    # the honest equivalent — if the universe composition changes by even
    # one symbol between two runs, the fingerprint changes too, which is
    # exactly the property a dataset fingerprint needs to have.
    data_fingerprint = fingerprint_dataset(
        source="binance_spot_klines_cross_sectional",
        symbol=",".join(sorted(panel["asset"].unique())),
        venue="binance",
        timeframe="1h",
        start_date=str(panel["timestamp"].min().date()),
        end_date=str(panel["timestamp"].max().date()),
        row_count=len(panel),
    )
    logger.info(f"  Dataset fingerprint: {data_fingerprint}")

    if config is None:
        # claude code changed: real 1h data over ~5 years (~43,800 candles)
        # per asset — sized so an expanding-window walk-forward gets a
        # genuine multi-fold OOS history, not a single fold. 1 year train
        # floor, 30-day test windows, 1h purge (matches forward_return_1h's
        # own horizon), no embargo (no known additional lookback beyond the
        # 1h label horizon in these features).
        config = WalkForwardConfig(
            mode="expanding", min_train_periods=8760, test_periods=720,
            horizon=1, purge_periods=1, embargo_periods=0, min_test_periods=168, seed=42,
        )

    # claude code changed: new — one checkpoint subdirectory per
    # hypothesis, so testing multiple hypotheses (cs_rank, cs_zscore, ...)
    # never collide on the same topk_{k}.json files.
    #
    # claude code changed: namespaced by rebalance_frequency too (rf{N}
    # subfolder) — a real correctness bug this prevents: without this,
    # re-running with the new realistic rebalance_frequency=24 default
    # would have silently LOADED the old checkpoints computed under the
    # broken implicit rebalance_frequency=1 (hourly full-turnover)
    # behavior instead of recomputing, since the old path had no
    # awareness of which cost assumption a cached result was computed
    # under.
    checkpoint_dir = (
        str(Path(research_data_dir) / "cross_sectional_checkpoints" / hypothesis_name / f"rf{rebalance_frequency}")
        if checkpoint else None
    )
    sweep = run_topk_sweep(
        panel, "timestamp", "asset", "feature", "forward_return", config,
        top_k_values=top_k_values, long_short=long_short, cost_rate=cost_rate,
        n_permutations=n_permutations, random_seed=config.seed,
        strategy_name=hypothesis_name, strategy_version="v1",
        rebalance_frequency=rebalance_frequency, data_fingerprint=data_fingerprint,
        checkpoint_dir=checkpoint_dir, force_recompute=force_recompute,
    )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hypothesis_name": hypothesis_name,
        "feature_col": feature_col,
        "n_assets": panel["asset"].nunique(),
        "n_timestamps": panel["timestamp"].nunique(),
        "data_fingerprint": data_fingerprint,   # claude code changed: new — see this function's own comment above
        "rebalance_frequency": rebalance_frequency,   # claude code changed: new — provenance, so a reader of this verdict file can see which cost/turnover assumption produced it
        "top_k_values": sweep["top_k_values"],
        "any_survivor": sweep["any_survivor"],
        "per_config": {
            k: {"real": v["real"], "verdict": v["verdict"]}
            for k, v in sweep["per_config"].items()
        },
    }

    out_path = Path(verdict_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("\n" + "=" * 70)
    logger.info(f"CROSS-SECTIONAL VERDICT ({hypothesis_name}): "
                f"{'SURVIVOR' if sweep['any_survivor'] else 'NO_SURVIVORS'}")
    logger.info(f"Saved: {out_path}")
    logger.info("=" * 70)

    return output


if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.run_cross_sectional_oos
    """
    run_cross_sectional_research(hypothesis_name="cross_sectional_zscore")
