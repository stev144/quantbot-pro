# bot/quant_metrics/__init__.py  # claude code changed: 2026-10-28 — new: institutional market metrics (five-gate reviewed)
"""Institutional quantitative market metrics for crypto and forex instruments.

Design and the five-gate review (which metrics were built, modified or rejected, and why) live in
QUANT_METRICS_FIVE_GATE_REVIEW_2026-10-28.md. Every metric here is DESCRIPTIVE: none is a validated trading signal
(CRRP-2026-10-02: no strategy has canonical edge evidence).

    statistics  Newey–West standard error, Lo–MacKinlay variance ratio, Sharpe ratio with standard error
    factors     rolling z-score, efficiency ratio, trend quality, realised volatility, momentum, Amihud illiquidity
    regimes     z-score / volatility / variance-ratio regime labels
    ranking     cross-sectional and time-series percentile ranks
    api         per-symbol metrics and universe-level ranks (the only entry points pages should call)
"""
