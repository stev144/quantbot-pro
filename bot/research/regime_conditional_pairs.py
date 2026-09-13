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

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bot.research.cointegration_engine import MIN_CANDLES, CointegrationEngine, PairResult
from bot.research.regime_labels import label_regime_episodes


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
