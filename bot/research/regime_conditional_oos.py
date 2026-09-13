# ============================================================
# bot/research/regime_conditional_oos.py
#
# REGIME-CONDITIONAL OOS VALIDATION (Phase 11 — mission's own "mandatory")
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission. Answers Phase 11's exact question: "Does the relationship
# survive unseen data WITHIN THE SAME MARKET REGIME?" — strictly stronger
# than the whole-dataset OOS result oos_validator.py already produces.
#
# DESIGN — purely additive, zero changes to oos_validator.py's core logic:
#   1. Call evaluate_cross_sectional_oos() completely UNMODIFIED to get a
#      real OOSResult. Its cross_sectional_ranking evaluation type already
#      stores every fold's per-period records (FoldEvalResult.trades),
#      each carrying a real "timestamp" key (oos_validator.py:1320) — this
#      was already there, requiring NO change to get regime-sliceable data.
#   2. For each fold's test-window period_records, look up each record's
#      OWN timestamp in the pre-computed, CAUSAL regime label series
#      (bot.research.regime_labels.compute_regime_labels — computed once,
#      OUTSIDE this function, on the full causal history, so a regime
#      label at time t never depends on anything after t; slicing it by
#      fold/regime after the fact cannot introduce look-ahead, since the
#      label itself never changes based on how it's grouped afterward).
#   3. Recompute the SAME real economic-metrics formula
#      (oos_validator.compute_cross_sectional_metrics_from_records) on
#      each regime's subset of that fold's records — same compounding
#      equity curve, same real turnover-scaled cost, same Sharpe/hit-rate/
#      drawdown math the whole-dataset OOS result already uses. No new
#      metric definitions invented for the regime-conditional case.
#
# This satisfies Phase 11's explicit checklist:
#   - regime info constructed only from information available at that
#     point (the label series is causal by construction, see
#     regime_labels.py's own no-look-ahead test)
#   - OOS observations assigned to regimes (this module's core loop)
#   - metrics calculated separately by regime (this module's output)
#   - chronological ordering preserved (fold boundaries/purge/embargo are
#     never touched — this module only ever further SUBDIVIDES an
#     already-built fold's test window, never re-orders or re-splits it)
#   - universe timing / transaction costs preserved (same cost_rate,
#     same turnover_fraction accounting, reused verbatim)
#
# Phase 13 (economic validity by regime) is answered by the SAME output —
# mean_net_return / hit_rate_pct / sharpe_ratio / max_drawdown_pct /
# total_cost_paid_pct per regime ARE the economic-significance numbers
# Phase 13 asks for; no separate economic engine was built for this,
# per the mission's "use existing infrastructure" rule.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from bot.research.oos_validator import OOSResult, compute_cross_sectional_metrics_from_records
from bot.research.regime_labels import MIN_REGIME_OBS, REGIME_TAXONOMY_VERSION


@dataclass
class RegimeFoldMetrics:
    """One fold's economic metrics, restricted to one regime's subset of
    that fold's TEST-window records."""
    fold_id: int
    regime: str
    n_periods: int
    sufficient_sample: bool
    metrics: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "fold_id": self.fold_id, "regime": self.regime,
            "n_periods": self.n_periods, "sufficient_sample": self.sufficient_sample,
            "metrics": self.metrics,
        }


@dataclass
class RegimeConditionalOOSResult:
    """claude code changed: new. Mirrors OOSResult's own shape (fold-level
    detail always present, pooled aggregate a convenience derived FROM it,
    never a replacement) but keyed by regime. `whole_dataset` carries the
    UNMODIFIED OOSResult.aggregate this was built from, so a reader always
    has the whole-dataset baseline sitting right next to the regime
    breakdown — Phase 17's "overall result" + "regime breakdown" side by
    side, not two disconnected reports."""
    taxonomy_version: str
    regime_dimension: str            # which label column was used: "trend_state" | "volatility_state" | "regime_label"
    cost_rate: float
    whole_dataset: Dict
    per_fold_per_regime: List[RegimeFoldMetrics] = field(default_factory=list)
    pooled_by_regime: Dict[str, Dict] = field(default_factory=dict)
    min_obs: int = MIN_REGIME_OBS

    def to_dict(self) -> Dict:
        return {
            "taxonomy_version": self.taxonomy_version,
            "regime_dimension": self.regime_dimension,
            "cost_rate": self.cost_rate,
            "min_obs": self.min_obs,
            "whole_dataset": self.whole_dataset,
            "per_fold_per_regime": [f.to_dict() for f in self.per_fold_per_regime],
            "pooled_by_regime": self.pooled_by_regime,
        }


def evaluate_cross_sectional_oos_by_regime(
    oos_result: OOSResult,
    regime_labels: pd.Series,
    regime_dimension: str,
    cost_rate: float,
    fold_initial_balance: float = 10_000.0,
    min_obs: int = MIN_REGIME_OBS,
) -> RegimeConditionalOOSResult:
    """
    Parameters
    ----------
    oos_result : OOSResult
        The output of calling evaluate_cross_sectional_oos() UNMODIFIED —
        must be evaluation_type == "cross_sectional_ranking" (the only
        type whose FoldEvalResult.trades carries timestamped per-period
        records today).
    regime_labels : pd.Series
        A single label column (trend_state, volatility_state, or the
        combined regime_label) from regime_labels.compute_regime_labels(),
        indexed by the SAME timestamps the OOS panel used. Rows the label
        series marks NaN (warmup/missing) are excluded from every regime
        bucket, never silently folded into an existing regime.
    regime_dimension : str
        A human-readable name for which column was passed (for the report
        only — this function doesn't re-derive it, since a Series has no
        column name once extracted from its parent DataFrame reliably).
    cost_rate : float
        Same cost_rate the original evaluate_cross_sectional_oos() call
        used — passed explicitly (not read off oos_result, which doesn't
        store it) so the regime-conditional cost accounting is provably
        identical to the whole-dataset run's, never a second, drifting
        cost assumption.
    """
    if oos_result.evaluation_type != "cross_sectional_ranking":
        raise ValueError(
            f"evaluate_cross_sectional_oos_by_regime requires evaluation_type='cross_sectional_ranking', "
            f"got {oos_result.evaluation_type!r} — only this type's FoldEvalResult.trades carries timestamped records"
        )

    per_fold_per_regime: List[RegimeFoldMetrics] = []
    pooled_records_by_regime: Dict[str, List[Dict]] = {}

    for fold_result in oos_result.folds:
        if fold_result.skipped or not fold_result.trades:
            continue

        records_by_regime: Dict[str, List[Dict]] = {}
        for record in fold_result.trades:
            ts = record["timestamp"]
            regime = regime_labels.get(ts)
            if regime is None or (isinstance(regime, float) and pd.isna(regime)):
                continue  # warmup/missing regime label — never silently attributed to a regime
            records_by_regime.setdefault(regime, []).append(record)

        for regime, records in records_by_regime.items():
            periods_per_year_this_fold = (
                len(records) / max((fold_result.fold.test_end - fold_result.fold.test_start).days / 365.25, 1e-6)
                if records else None
            )
            metrics = compute_cross_sectional_metrics_from_records(records, cost_rate, fold_initial_balance, periods_per_year_this_fold)
            per_fold_per_regime.append(RegimeFoldMetrics(
                fold_id=fold_result.fold.fold_id, regime=regime,
                n_periods=len(records), sufficient_sample=len(records) >= min_obs,
                metrics=metrics,
            ))
            pooled_records_by_regime.setdefault(regime, []).extend(records)

    pooled_by_regime: Dict[str, Dict] = {}
    for regime, records in pooled_records_by_regime.items():
        # claude code changed: pooled periods_per_year uses the records'
        # OWN timestamp span, the same convention _aggregate_cross_sectional
        # implicitly relies on via per-fold annualisation — computed here
        # explicitly since pooling crosses fold boundaries.
        timestamps = pd.to_datetime([r["timestamp"] for r in records])
        span_years = max((timestamps.max() - timestamps.min()).days / 365.25, 1e-6)
        periods_per_year = len(records) / span_years if records else None
        metrics = compute_cross_sectional_metrics_from_records(records, cost_rate, fold_initial_balance, periods_per_year)
        pooled_by_regime[regime] = {
            "n_periods": len(records),
            "sufficient_sample": len(records) >= min_obs,
            "metrics": metrics,
        }

    return RegimeConditionalOOSResult(
        taxonomy_version=REGIME_TAXONOMY_VERSION,
        regime_dimension=regime_dimension,
        cost_rate=cost_rate,
        whole_dataset=oos_result.aggregate,
        per_fold_per_regime=per_fold_per_regime,
        pooled_by_regime=pooled_by_regime,
        min_obs=min_obs,
    )
