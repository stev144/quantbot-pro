# ============================================================
# bot/research/regime_conditional_pairs.py
#
# REGIME-CONDITIONAL COINTEGRATION, HEDGE RATIO, AND KALMAN (Phases 4, 5, 6)
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission.
#
# A REAL METHODOLOGICAL CHOICE, STATED EXPLICITLY (per the mission's own
# demand for documented methodology): cointegration/ADF testing assumes a
# genuinely time-ordered, unbroken process. Naively concatenating every
# row that shares a regime label — even when those rows come from
# non-adjacent time periods — would splice together unrelated windows and
# manufacture artificial discontinuities exactly at each splice boundary,
# biasing the ADF test in an unknown direction. This module therefore
# tests cointegration only within CONTIGUOUS regime episodes
# (bot.research.regime_labels.label_regime_episodes), never across a
# Frankenstein-spliced series — Phase 4/5's "regime transitions" and
# "relationship stability" questions are answered by comparing episodes
# to each other and to the full-sample result, not by pretending
# disconnected episodes are one continuous series.
#
# This also means an HONEST, EXPECTED FINDING is possible and must be
# reported as such, not treated as an implementation failure: if regime
# episodes on the available data are shorter than
# cointegration_engine.MIN_CANDLES (1,000 candles — the engine's own,
# pre-existing floor for a trustworthy test, NOT lowered here), there may
# be ZERO qualifying episodes. That is itself a real, reportable Phase 4/5
# result ("regimes on this timeframe/asset don't persist long enough for
# within-episode cointegration testing"), not a bug to work around by
# loosening the engine's floor — the mission's absolute rules forbid
# exactly that.
#
# KALMAN IS DIFFERENT, AND HANDLED DIFFERENTLY (Phase 5's "does dynamic
# hedge-ratio estimation become more valuable during regime transitions?"):
# a Kalman filter's state evolves recursively and MUST run over a
# continuous series to mean anything — segmenting it per episode and
# reinitializing state at each boundary would throw away exactly the
# cross-episode continuity that makes a dynamic hedge ratio interesting in
# the first place. So Kalman is run ONCE, unmodified, over the full
# aligned price series (kalman_filter_engine.run_on_prices(), already
# proven causal — kalman_beta_pred is explicitly the leakage-free,
# pre-update estimate), and its OUTPUT is grouped by regime label
# POST HOC. This is safe (unlike a naive input-level splice) because
# grouping an already-computed, already-causal per-timestamp OUTPUT
# by an independent, already-causal per-timestamp LABEL introduces no
# new information at any point — it is exactly as safe as, and no
# different from, this mission's other post-hoc regime-conditional
# analyses (IC-by-regime, OOS-by-regime).
# ============================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, symbols_for_asset_class
from bot.research.cointegration_engine import MIN_CANDLES, CointegrationEngine, PairResult
from bot.research.data_access import load_ohlcv_via_provider  # claude code changed: Data-Layer Audit migration (F.3) — was a private copy in this file, now the shared helper (see that module's docstring)
from bot.research.regime_labels import compute_regime_labels, label_regime_episodes

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@dataclass
class EpisodeCointegrationResult:
    regime: str
    start: pd.Timestamp
    end: pd.Timestamp
    length: int
    result: Optional[PairResult] = None
    status: str = "OK"   # "OK" | "TOO_SHORT"

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime, "start": str(self.start), "end": str(self.end), "length": self.length,
            "status": self.status,
            "is_cointegrated": self.result.is_cointegrated if self.result else None,
            "adf_pvalue": self.result.adf_pvalue if self.result else None,
            "hedge_ratio": self.result.hedge_ratio if self.result else None,
            "half_life": self.result.half_life if self.result else None,
        }


@dataclass
class RegimeConditionalCointegrationReport:
    symbol_a: str
    symbol_b: str
    full_sample: PairResult
    min_episode_length: int
    episodes: List[EpisodeCointegrationResult] = field(default_factory=list)
    by_regime_summary: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "symbol_a": self.symbol_a, "symbol_b": self.symbol_b,
            "min_episode_length": self.min_episode_length,
            "full_sample": {
                "is_cointegrated": self.full_sample.is_cointegrated,
                "adf_pvalue": self.full_sample.adf_pvalue,
                "coint_pvalue": self.full_sample.coint_pvalue,
                "hedge_ratio": self.full_sample.hedge_ratio,
                "half_life": self.full_sample.half_life,
            },
            "n_episodes_total": len(self.episodes),
            "n_episodes_qualifying": sum(1 for e in self.episodes if e.status == "OK"),
            "episodes": [e.to_dict() for e in self.episodes],
            "by_regime_summary": self.by_regime_summary,
        }


def regime_conditional_cointegration(
    engine: CointegrationEngine,
    symbol_a: str,
    symbol_b: str,
    price_a: pd.Series,
    price_b: pd.Series,
    regime_labels: pd.Series,
    min_episode_length: int = MIN_CANDLES,
) -> RegimeConditionalCointegrationReport:
    """
    Phase 4. `price_a`/`price_b` must be the same shape
    engine._test_pair() already accepts elsewhere in this codebase (real,
    non-log close prices — _test_pair applies its own log transform
    internally... NOTE: callers already log-transforming before calling
    _test_pair() (see bot/research_lab/tools/research_tools.py's fix
    earlier this session) must pass log prices here too, for the exact
    same reason — this function does not re-derive that convention, it
    only slices whatever price series the caller already correctly built.
    """
    full_sample = engine._test_pair(symbol_a, symbol_b, price_a, price_b)

    episodes_df = label_regime_episodes(regime_labels)
    episode_results: List[EpisodeCointegrationResult] = []
    for _, ep in episodes_df.iterrows():
        if ep["length"] < min_episode_length:
            episode_results.append(EpisodeCointegrationResult(
                regime=ep["regime"], start=ep["start"], end=ep["end"], length=int(ep["length"]), status="TOO_SHORT",
            ))
            continue
        window_a = price_a.loc[ep["start"]:ep["end"]]
        window_b = price_b.loc[ep["start"]:ep["end"]]
        result = engine._test_pair(symbol_a, symbol_b, window_a, window_b)
        episode_results.append(EpisodeCointegrationResult(
            regime=ep["regime"], start=ep["start"], end=ep["end"], length=int(ep["length"]), result=result, status="OK",
        ))

    by_regime_summary: Dict[str, Dict] = {}
    for regime in sorted({e.regime for e in episode_results}):
        qualifying = [e for e in episode_results if e.regime == regime and e.status == "OK"]
        if not qualifying:
            by_regime_summary[regime] = {
                "n_qualifying_episodes": 0,
                "status": "INSUFFICIENT_SAMPLE",
                "reason": f"no contiguous {regime} episode reached min_episode_length={min_episode_length}",
            }
            continue
        hedge_ratios = [e.result.hedge_ratio for e in qualifying]
        half_lives = [e.result.half_life for e in qualifying if np.isfinite(e.result.half_life)]
        by_regime_summary[regime] = {
            "n_qualifying_episodes": len(qualifying),
            "status": "OK",
            "fraction_cointegrated": float(np.mean([e.result.is_cointegrated for e in qualifying])),
            "hedge_ratio_mean": float(np.mean(hedge_ratios)),
            "hedge_ratio_std": float(np.std(hedge_ratios, ddof=1)) if len(hedge_ratios) >= 2 else 0.0,
            "hedge_ratio_min": float(np.min(hedge_ratios)),
            "hedge_ratio_max": float(np.max(hedge_ratios)),
            "half_life_mean": float(np.mean(half_lives)) if half_lives else None,
        }

    return RegimeConditionalCointegrationReport(
        symbol_a=symbol_a, symbol_b=symbol_b, full_sample=full_sample,
        min_episode_length=min_episode_length, episodes=episode_results, by_regime_summary=by_regime_summary,
    )


def regime_conditional_kalman_hedge_ratio(
    kalman_output: pd.DataFrame,
    regime_labels: pd.Series,
    beta_col: str = "kalman_beta_pred",
    min_obs: int = 30,
) -> Dict:
    """
    Phase 5's Kalman deliverable. `kalman_output` is the UNMODIFIED return
    of KalmanPairsFilter.run_on_prices() — run ONCE over the full
    continuous series by the caller, never re-run per episode (see this
    module's header for why). `beta_col` defaults to kalman_beta_pred,
    the leakage-free pre-update estimate — this function does not accept
    the posterior kalman_beta by accident; a caller must explicitly pass
    beta_col="kalman_beta" to use it, and should have a real reason to.

    Groups the already-computed, already-causal per-timestamp beta
    trajectory by the (independently causal) regime label at that same
    timestamp — safe post-hoc grouping, not a re-run.
    """
    if beta_col not in kalman_output.columns:
        raise ValueError(f"kalman_output has no column {beta_col!r} — available: {list(kalman_output.columns)}")

    merged = kalman_output[[beta_col]].join(regime_labels.rename("_regime"), how="inner")
    merged = merged.dropna(subset=["_regime", beta_col])

    full_sample = {
        "n_obs": len(kalman_output),
        "beta_mean": float(kalman_output[beta_col].mean()),
        "beta_std": float(kalman_output[beta_col].std()) if len(kalman_output) >= 2 else 0.0,
        "beta_min": float(kalman_output[beta_col].min()),
        "beta_max": float(kalman_output[beta_col].max()),
    }

    by_regime: Dict[str, Dict] = {}
    for regime in sorted(merged["_regime"].unique()):
        sub = merged.loc[merged["_regime"] == regime, beta_col]
        n_obs = len(sub)
        sufficient = n_obs >= min_obs
        if not sufficient:
            by_regime[regime] = {"n_obs": n_obs, "sufficient_sample": False, "status": "INSUFFICIENT_SAMPLE"}
            continue
        by_regime[regime] = {
            "n_obs": n_obs, "sufficient_sample": True, "status": "OK",
            "beta_mean": float(sub.mean()), "beta_std": float(sub.std()) if n_obs >= 2 else 0.0,
            "beta_min": float(sub.min()), "beta_max": float(sub.max()),
            "beta_range": float(sub.max() - sub.min()),
        }

    # claude code changed: real cross-regime stability metric — Phase 5's
    # "is the hedge ratio stable across regimes, or does the relationship
    # structurally change?" answered directly: the spread of per-regime
    # MEANS relative to the full-sample std. A small value means every
    # regime centers on roughly the same hedge ratio (stable); a large
    # value means the relationship itself shifts by regime (structural
    # change, not just noise).
    regime_means = [v["beta_mean"] for v in by_regime.values() if v.get("status") == "OK"]
    cross_regime_instability = None
    if len(regime_means) >= 2 and full_sample["beta_std"] > 1e-12:
        cross_regime_instability = float(np.std(regime_means, ddof=1) / full_sample["beta_std"])

    return {
        "beta_col": beta_col,
        "full_sample": full_sample,
        "by_regime": by_regime,
        "cross_regime_instability_ratio": cross_regime_instability,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNABLE DRIVER
#
# claude code changed: real bug fix — this module previously defined only
# importable functions/dataclasses with no __main__ entry point at all, so
# `python -m bot.research.regime_conditional_pairs` imported the module,
# executed nothing, and exited cleanly with no output — not a crash, just
# nothing to run. The only existing caller anywhere in the codebase was
# this module's own test file (test_regime_conditional_pairs.py), which
# builds synthetic data directly rather than going through a CLI. This
# adds the missing driver, following cointegration_engine.py's own
# __main__ (run_cointegration_research()) CSV-loading convention exactly,
# so both scripts behave the same way for the same data/ directory.
#
# claude code changed: Data-Layer Architecture Audit migration (F.3 proof
# of concept — see DATA_LAYER_ARCHITECTURE_AUDIT.md). This driver no
# longer reads data/*.csv directly; it goes through the canonical
# MarketDataProvider contract instead. This is the first research-side
# consumer of get_historical_bars() anywhere in the codebase — every
# other research engine still reads CSVs/calls fetchers directly (see the
# audit doc's dependency list, section D). Confirmed zero-risk to migrate:
# grepped the whole repo first — this module's only test
# (test_regime_conditional_pairs.py) imports regime_conditional_cointegration/
# regime_conditional_kalman_hedge_ratio directly with synthetic data and
# never touches this driver or _load_ohlcv_csv, so no existing test
# depends on the old CSV-reading behavior being replaced here.
# ═══════════════════════════════════════════════════════════════════════════════

def run_regime_conditional_research(
    asset_class: str = ASSET_CLASS_CRYPTO,
    interval: str = "1h",
    min_episode_length: int = MIN_CANDLES,
    universe: Optional[List[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, RegimeConditionalCointegrationReport]:
    """
    Real, runnable driver. `universe` defaults to CointegrationEngine's own
    default (crypto, see that module's UNIVERSE constant) — pass an
    explicit list (e.g. bot.instruments.symbols_for_asset_class(ASSET_CLASS_FOREX),
    "/" replaced with "_" to match this file-naming convention) to run
    against a different asset class or a smaller/faster subset; nothing
    here assumes crypto beyond that inherited default.

    Two-stage, deliberately not "test every pair regime-conditionally" —
    that would mean paying for a full per-episode ADF test on every
    candidate pair, most of which aren't even cointegrated over the full
    sample, which is not a meaningful question to ask regime-by-regime.
    Instead:

    1. Run the ordinary CointegrationEngine.run_all() (full-sample,
       already FDR- and OOS-persistence-corrected) to find which pairs
       are genuinely cointegrated overall.
    2. For each of THOSE pairs only, run regime_conditional_cointegration()
       to check whether that relationship holds up within each regime
       episode, using regime labels computed from symbol_a's own OHLCV
       via regime_labels.compute_regime_labels() (real, causal, already
       tested — not a placeholder). Tagging a pair by symbol_a's regime
       is a real, named choice, not the only valid one — a shared
       benchmark regime series would be an equally defensible
       alternative; this keeps the driver self-contained without
       requiring the caller to supply one.

    `start`/`end` default to a 5-year lookback ending now, matching this
    codebase's existing data/*.csv history depth — pass explicit values to
    request a narrower/different window. Data now comes live from
    BinanceKlinesProvider/YahooForexProvider (see _load_ohlcv_via_provider)
    rather than from data/*.csv, so results reflect whatever the venue
    returns for the requested window at run time, not a static snapshot.
    """
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=5 * 365)

    engine = CointegrationEngine(universe=universe)
    data: Dict[str, pd.DataFrame] = {}
    for symbol in engine.universe:
        df = load_ohlcv_via_provider(symbol, asset_class, interval, start, end)
        if df is not None:
            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

    if not data:
        logger.warning(
            f"No OHLCV data retrieved from the {asset_class} provider for interval "
            f"{interval!r} over [{start.date()}, {end.date()}] — nothing to test."
        )
        return {}

    pairs_df, _ = engine.run_all(data)
    valid_pairs = pairs_df[pairs_df["passes_filters"]] if not pairs_df.empty else pairs_df

    if valid_pairs.empty:
        logger.info(
            "\nNo pair passed full-sample cointegration (in-sample ADF + FDR + "
            "half-life + out-of-sample persistence) — nothing to test regime-"
            "conditionally. This is a real, reportable result, not an error."
        )
        return {}

    reports: Dict[str, RegimeConditionalCointegrationReport] = {}
    logger.info(f"\n{len(valid_pairs)} full-sample-cointegrated pair(s) — running regime-conditional analysis...")
    logger.info("=" * 70)

    for _, row in valid_pairs.iterrows():
        symbol_a, symbol_b = row["symbol_a"], row["symbol_b"]
        df_a, df_b = data[symbol_a], data[symbol_b]

        regime_labels = compute_regime_labels(df_a)["regime_label"]
        log_a = np.log(df_a["close"])
        log_b = np.log(df_b["close"])

        aligned_index = log_a.index.intersection(log_b.index).intersection(regime_labels.dropna().index)
        if len(aligned_index) < min_episode_length:
            logger.info(f"\n{row['pair_name']}: SKIPPED — only {len(aligned_index)} rows with valid price+regime data")
            continue

        report = regime_conditional_cointegration(
            engine, symbol_a, symbol_b,
            log_a.loc[aligned_index], log_b.loc[aligned_index], regime_labels.loc[aligned_index],
            min_episode_length=min_episode_length,
        )
        reports[row["pair_name"]] = report

        logger.info(f"\n{row['pair_name']} — full sample: cointegrated={report.full_sample.is_cointegrated}, "
                    f"half_life={report.full_sample.half_life:.1f} candles")
        for regime, summary in sorted(report.by_regime_summary.items()):
            if summary["status"] == "OK":
                logger.info(
                    f"    {regime:28} n_episodes={summary['n_qualifying_episodes']:>2} "
                    f"frac_cointegrated={summary['fraction_cointegrated']:.2f} "
                    f"half_life_mean={summary['half_life_mean']}"
                )
            else:
                logger.info(f"    {regime:28} {summary['status']} — {summary['reason']}")

    return reports


# claude code changed: Data-Layer Architecture Audit migration (F.3) —
# --asset-class now selects a provider (_PROVIDERS) instead of a data_dir;
# crypto/forex CSVs no longer need to be pre-fetched onto disk before
# running this driver (the old data_dir split existed only because
# data/{SYM}_{interval}.csv vs data/forex/{SYM}_{interval}.csv were two
# different directories to read from — moot now that both asset classes
# go through get_historical_bars() instead).
_ASSET_CLASSES_BY_CLI_NAME = {"crypto": ASSET_CLASS_CRYPTO, "forex": ASSET_CLASS_FOREX}

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regime-conditional cointegration research driver.")
    parser.add_argument("--asset-class", choices=sorted(_ASSET_CLASSES_BY_CLI_NAME), default="crypto")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--min-episode-length", type=int, default=MIN_CANDLES)
    parser.add_argument("--lookback-days", type=int, default=5 * 365, help="History window ending now.")
    args = parser.parse_args()

    asset_class = _ASSET_CLASSES_BY_CLI_NAME[args.asset_class]
    universe = [s.replace("/", "_") for s in symbols_for_asset_class(asset_class)]
    run_end = datetime.now(timezone.utc)
    run_start = run_end - timedelta(days=args.lookback_days)

    run_regime_conditional_research(
        asset_class=asset_class, interval=args.interval,
        min_episode_length=args.min_episode_length, universe=universe,
        start=run_start, end=run_end,
    )
