# ============================================================
# bot/views/forex_terminal_data.py
# claude code changed: new file — Forex Research Dashboard mission.
# Mirrors bot/views/terminal_data.py's one-function-per-section shape,
# but built honestly around what Forex research actually has today
# rather than forcing reuse of crypto-shaped functions that assume data
# that doesn't exist for Forex (research_data/*_cross_section.csv,
# *_validated_features.csv, and any bot.backtesting.backtester.backtest()
# result — that function has zero Forex cost-model awareness, see this
# module's own docstrings below and the approved plan for the full
# forensic finding). Every function here either reads real backend state
# or returns an explicit {"available": False, "reason": ...} marker —
# same convention terminal_data.py already established, never a made-up
# number.
#
# Deliberately NOT wired into terminal_data.py itself — that module's
# functions are tested, working, and crypto-specific by construction;
# this file reuses its genuinely asset-agnostic pieces
# (get_market_state, the generalized _compute_price_correlation) rather
# than editing their crypto call sites.
# ============================================================

import os
from datetime import datetime, timezone

import pandas as pd

from bot.instruments import ASSET_CLASS_FOREX, get_instrument, symbols_for_asset_class
from bot.research_lab.data_fingerprint import fingerprint_dataset
from bot.views.terminal_data import DATA_DIR, _compute_price_correlation

FOREX_DATA_DIR = os.path.join(DATA_DIR, "forex")


# ============================================================
# DATA PROVENANCE
# ============================================================
def get_forex_data_provenance(symbol, instrument, df):
    """
    New section, no Crypto equivalent — Crypto has one centralized
    exchange (Binance) so provenance is rarely in question; Forex is
    OTC/provider-dependent, so the mission asks this to be surfaced
    explicitly. Real: provider/venue come straight from the Instrument
    registry row (bot/instruments.py's _build_forex_registry()), never
    hardcoded per-call. Freshness/row-count/last-bar come from the
    actual loaded DataFrame — an empty df produces an honest "no local
    data cached yet" state, never a fabricated timestamp.
    """
    if df is None or df.empty:
        return {
            "available": False,
            "reason": f"No local data cached yet for {symbol} — run bot/forex_data_fetcher.py to populate data/forex/",
            "provider": instrument.venue,
            "symbol": symbol,
            "timeframe": instrument.timeframe,
        }

    from bot.forex_data_fetcher import FRESHNESS_MAX_AGE_DAYS

    last_ts = df.index[-1]
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")
    age_days = (datetime.now(timezone.utc) - last_ts).total_seconds() / 86400
    is_fresh = age_days <= FRESHNESS_MAX_AGE_DAYS

    fingerprint = fingerprint_dataset(
        source=instrument.data_source or "unknown",
        symbol=symbol,
        venue=instrument.venue or "unknown",
        timeframe=instrument.timeframe or "unknown",
        start_date=str(df.index[0].date()),
        end_date=str(last_ts.date()),
        row_count=len(df),
    )

    return {
        "available": True,
        "provider": instrument.venue,
        "symbol": symbol,
        "timeframe": instrument.timeframe,
        "row_count": len(df),
        "last_bar_utc": last_ts,
        "age_days": round(age_days, 1),
        "is_fresh": is_fresh,
        "fingerprint": fingerprint,
    }


# ============================================================
# RESEARCH STATE
# ============================================================
def _forex_experiments():
    """
    claude code changed: real gap found while building this — a
    ResearchExperiment's structured_spec["asset_class"] field is almost
    always None by design (bot/research_lab/spec.py's resolved_asset_class()
    infers it from the instrument registry rather than storing it
    redundantly — see that method's own docstring), so a naive DB filter
    on structured_spec__asset_class would silently miss every real Forex
    experiment. Resolves the asset class the same way the rest of the
    platform does: look up the stored `asset` symbol in the instrument
    registry. Experiment volume on this platform is small (tens, not
    thousands), so doing this resolution in Python per page load is
    cheap — no need for a DB-side computed column.
    """
    from bot.research_lab.models import ResearchExperiment

    forex_symbols = set(symbols_for_asset_class(ASSET_CLASS_FOREX))
    matched = []
    for exp in ResearchExperiment.objects.all().only(
        "id", "status", "verdict", "structured_spec", "statistical_results", "created_at"
    ):
        asset = (exp.structured_spec or {}).get("asset")
        if asset in forex_symbols:
            matched.append(exp)
    return matched


def get_forex_research_state():
    """
    Real counts from the Research Lab's own experiment history — unlike
    Crypto's file-count approach (research_data/*.csv), Forex research to
    date genuinely IS the Research Lab pairs/cointegration pipeline, so
    this is the honest, meaningful equivalent rather than a forced
    reuse of a metric shape that doesn't fit. features_evaluated/
    strategies_active are explicitly not available — feature_validator.py
    has never been run against Forex data, and no Forex-specific
    strategy exists (bot.engines.strategy_router.StrategyRouter only
    ever routes to MovingAverageStrategy/MeanReversionStrategy, neither
    validated for Forex).
    """
    experiments = _forex_experiments()

    status_counts = {}
    verdict_counts = {}
    for exp in experiments:
        status_counts[exp.status] = status_counts.get(exp.status, 0) + 1
        if exp.verdict:
            verdict_counts[exp.verdict] = verdict_counts.get(exp.verdict, 0) + 1

    latest_run = max((exp.created_at for exp in experiments), default=None)
    age_days = (datetime.now(timezone.utc) - latest_run).days if latest_run else None

    return {
        "total_experiments": len(experiments),
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "latest_run": latest_run,
        "age_days": age_days,
        "asset_count": len(symbols_for_asset_class(ASSET_CLASS_FOREX)),
        "features_evaluated": {
            "available": False,
            "reason": "feature_validator.py has never been run against Forex data",
        },
        "strategies_active": {
            "available": False,
            "reason": "No Forex-validated strategy exists — StrategyRouter only routes to strategies never tested against Forex transaction costs",
        },
    }


# ============================================================
# ALPHA INTELLIGENCE
# ============================================================
def get_forex_alpha_intelligence():
    """
    Real: every completed Forex pairs/cointegration experiment's own
    stored statistical_results (written by cointegration_engine.py's
    _test_pair(), the same engine the full universe sweep uses — see
    today's log-price fix). This is genuine Forex "alpha intelligence"
    content without touching bot.backtesting.backtester at all.
    Feature-predictive-power / strategy-expectancy fields stay explicitly
    unavailable — no Forex feature validation or strategy backtest exists.
    """
    experiments = _forex_experiments()
    pairs_results = []
    for exp in experiments:
        if exp.structured_spec.get("hypothesis_type") != "pairs":
            continue
        sr = exp.statistical_results or {}
        if not sr:
            continue
        pairs_results.append({
            "pair_name": sr.get("pair_name", exp.structured_spec.get("asset")),
            "verdict": exp.verdict or "PENDING",
            "is_cointegrated": sr.get("is_cointegrated"),
            "adf_pvalue": sr.get("adf_pvalue"),
            "hedge_ratio": sr.get("hedge_ratio"),
            "half_life_hours": sr.get("half_life_hours"),
            "passes_filters": sr.get("passes_filters"),
            "reject_reason": sr.get("reject_reason", ""),
        })

    pairs_results.sort(key=lambda r: (r["adf_pvalue"] is None, r["adf_pvalue"]))

    return {
        "pairs_available": bool(pairs_results),
        "pairs_results": pairs_results,
        "feature_power": {
            "available": False,
            "reason": "feature_validator.py has never been run against Forex data",
        },
        "strategy_expectancy": {
            "available": False,
            "reason": "No Forex-cost-aware backtest exists — bot.backtesting.backtester has no asset_class awareness (see System Health / architecture note)",
        },
    }


# ============================================================
# PORTFOLIO RISK
# ============================================================
def get_forex_portfolio_risk():
    """
    Real correlation via the same _compute_price_correlation() Crypto
    uses, generalized (this mission) to accept a directory — pointed at
    data/forex/ instead of duplicating the function. Adds a real
    currency-exposure tally (how many registered Forex majors touch each
    currency) directly from the instrument registry — answers the
    mission's "USD concentration across pairs" ask with actual registry
    data, not an invented risk score. max_drawdown/crash_risk stay
    unavailable — no Forex backtest exists to draw a drawdown from, and
    contagion_engine.py is unwired for every asset class, not just Forex.
    """
    correlation = _compute_price_correlation(directory=FOREX_DATA_DIR)

    currency_counts = {}
    for instrument in symbols_for_asset_class(ASSET_CLASS_FOREX):
        inst = get_instrument(instrument)
        for ccy in (inst.base_currency, inst.quote_currency):
            if ccy:
                currency_counts[ccy] = currency_counts.get(ccy, 0) + 1
    currency_exposure = sorted(
        [{"currency": c, "pair_count": n} for c, n in currency_counts.items()],
        key=lambda r: r["pair_count"], reverse=True,
    )

    return {
        "correlation": correlation,
        "currency_exposure": currency_exposure,
        "max_drawdown": {"available": False, "reason": "No Forex backtest exists"},
        "crash_risk": {"available": False, "reason": "contagion_engine.py exists but has no persisted output and is not wired into any pipeline (same as Crypto)"},
    }


# ============================================================
# EXECUTION
# ============================================================
def get_forex_execution_state():
    """
    Real TradeRecord query, scoped to registered Forex symbols
    (TradeRecord.symbol stores the same canonical "BASE/QUOTE" form the
    instrument registry uses, e.g. "EUR/USD" — confirmed against
    bot/journal/models.py). Will honestly show zero trades today: no
    Forex live/paper execution exists anywhere in this codebase, and
    none should be fabricated to fill this section.
    """
    from bot.journal.models import TradeRecord

    forex_symbols = symbols_for_asset_class(ASSET_CLASS_FOREX)
    try:
        open_positions = list(
            TradeRecord.objects.filter(symbol__in=forex_symbols, status="OPEN").order_by("-created_at")[:50]
        )
        closed = TradeRecord.objects.filter(symbol__in=forex_symbols).exclude(status="OPEN")
        realized_pnl = sum((t.net_pnl or 0.0) for t in closed)

        return {
            "available": True,
            "open_positions": open_positions,
            "open_count": len(open_positions),
            "total_closed": closed.count(),
            "realized_pnl": round(realized_pnl, 2),
            "mode": "NONE — no Forex execution adapter exists",
        }
    except Exception as e:
        return {"available": False, "reason": f"TradeRecord query failed: {e}"}


# ============================================================
# SYSTEM HEALTH
# ============================================================
_SEVERITY_TO_STATUS = {"GREEN": "ok", "YELLOW": "warn", "ORANGE": "warn", "RED": "err"}


def get_forex_system_health():
    """
    Reuses the real, already-built, page-load-safe Forex health checks
    from bot/management/commands/deep_health_check.py (built in an
    earlier mission, never wired into any page until now) — never a
    second health-check implementation. Deliberately calls only the 4
    confirmed zero-network checks; check_forex_provider_connectivity is
    explicitly excluded (that function's own docstring: "OPT-IN ONLY —
    never called unless --check-external is passed") and
    check_forex_dataset_quality is excluded per the approved plan's
    page-load-cost note (it reads each symbol's full CSV, not just the
    last row).
    """
    from bot.management.commands.deep_health_check import (
        check_forex_architecture,
        check_forex_capability_governance,
        check_forex_dataset_freshness,
        check_forex_dataset_fingerprint_reproducibility,
    )

    checks = []
    for check_fn in (
        check_forex_architecture,
        check_forex_dataset_freshness,
        check_forex_capability_governance,
        check_forex_dataset_fingerprint_reproducibility,
    ):
        try:
            findings = check_fn()
        except Exception as e:
            checks.append({"name": check_fn.__name__, "status": "err", "detail": str(e)[:120]})
            continue
        for finding in findings:
            checks.append({
                "name": f"{finding.check}",
                "status": _SEVERITY_TO_STATUS.get(finding.severity, "warn"),
                "detail": finding.actual[:160],
            })

    n_err = sum(1 for c in checks if c["status"] == "err")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    overall = "err" if n_err else ("warn" if n_warn else "ok")

    return {"checks": checks, "overall": overall}
