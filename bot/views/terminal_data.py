# ============================================================
# bot/views/terminal_data.py
# claude code changed: new file — real, non-fabricated data sources for
# the institutional-terminal dashboard redesign. Each function below
# either reads real backend state (DB, filesystem, already-computed
# backtest results) or returns an explicit {"available": False, ...}
# marker — never a made-up number. See the UI/UX audit this redesign
# is based on for what was and wasn't already surfaced anywhere.
#
# Kept as a separate module from bot/views/dashboard.py so the dashboard
# view function stays readable — one function per terminal section.
# ============================================================

import os
import glob
import time
import importlib
from datetime import datetime, timezone

import pandas as pd
from django.conf import settings
from django.db import connection

DATA_DIR = os.path.join(settings.BASE_DIR, "data")
RESEARCH_DATA_DIR = os.path.join(settings.BASE_DIR, "research_data")

# claude code changed: 2026-10-05 — research freshness is judged from research OUTPUT files only. Was the newest
# mtime of ANYTHING in research_data/ (documents and sub-folders included), so editing the governance log made the
# whole research pipeline read "FRESH".
_NON_OUTPUT_SUFFIXES = (".md", ".txt", ".log", ".provenance.json")


def _latest_research_output():   # claude code changed: 2026-10-05 — new; shared by Research State and System Health
    """(mtime, filename) of the newest research output file directly in research_data/, or (None, None)."""
    paths = [p for p in glob.glob(os.path.join(RESEARCH_DATA_DIR, "*"))
             if os.path.isfile(p) and not p.lower().endswith(_NON_OUTPUT_SUFFIXES)]
    if not paths:
        return None, None
    newest = max(paths, key=os.path.getmtime)
    return os.path.getmtime(newest), os.path.basename(newest)


# How stale research output can be before the dashboard calls it "STALE"
# rather than "FRESH". Not a precise SLA — just a sanity threshold so a
# multi-month-old research run doesn't silently read as current.
RESEARCH_FRESHNESS_DAYS = 14


# ============================================================
# SECTION 1: MARKET STATE
# ============================================================
def get_market_state(regime_result, cross_section_dispersion):
    """
    Real data only: regime_result comes from the same RegimeDetector
    already run for the main backtest (bot/engines/regime_detector.py).
    cross_section_dispersion is computed by get_cross_sectional_dispersion()
    below from real per-symbol research_data/*_cross_section.csv files.

    Liquidity conditions are deliberately marked unavailable.

    claude code changed: Kraken Multi-Venue Execution, Step 18 — updated
    this reason. A real order-book-based liquidity metric now exists
    (bot/engines/liquidity.py::compute_liquidity_snapshot(), fed by
    ExchangeAdapter.get_order_book(), Step 9) — the old reason ("no
    metric exists") went stale the moment that shipped. Still not wired
    in here: computing it would mean a live exchange round-trip inside
    this page's synchronous request, the exact same architectural
    constraint get_execution_state() below already documents for
    unrealized_pnl ("not implemented here since that's a real
    architectural decision — sync exchange call latency in a page load,
    not a one-line addition"). Same reasoning, same choice, applied here.
    """
    return {
        "regime": regime_result.regime,
        "confidence": regime_result.confidence,
        "adx": regime_result.adx,
        "atr_ratio": regime_result.atr_ratio,
        "ema_spread_pct": regime_result.ema_spread_pct,
        "bb_width_pct": regime_result.bb_width_pct,
        "volatility_extreme": regime_result.volatility_extreme,
        "summary": regime_result.summary,
        "cross_section_dispersion": cross_section_dispersion,
        "liquidity": {
            "available": False,
            "reason": (
                "A real order-book-based liquidity metric exists "
                "(bot/engines/liquidity.py, ExchangeAdapter.get_order_book()) "
                "but isn't computed here — would require a live exchange "
                "call inside this page's synchronous request, the same "
                "architectural constraint unrealized_pnl below documents."
            ),
        },
    }


def get_cross_sectional_dispersion():
    """
    Real, computed from research_data/*_cross_section.csv — each file
    already has a cs_zscore column (cross-sectional z-score of that
    symbol's return vs. the rest of the universe on that candle, written
    by bot/research/cross_section_engine.py). Dispersion = std dev of the
    latest cs_zscore across all symbols — a genuine cross-sectional
    dispersion reading, not a derived/invented figure.

    Reads only the last valid row of the 'cs_zscore' column per file
    (not the whole file) to keep this cheap on every dashboard load.
    """
    return cross_sectional_dispersion_at_common_timestamp(RESEARCH_DATA_DIR, top_n=None)   # claude code changed: 2026-10-05 — one shared implementation (also used by the Forex dashboard); 2026-10-22 — top_n 5 → None: every coin at the common hour, ranked by |z| (as Forex)


def cross_sectional_dispersion_at_common_timestamp(directory, *, top_n=5, asset_label="symbols",
                                                   no_files_reason=None):   # claude code changed: 2026-10-05 — new (was duplicated in forex_terminal_data.py)
    """Dispersion and |z|-ranked extremes of the latest cs_zscore in <directory>/*_cross_section.csv, using ONLY
    the symbols whose latest value is at the most common latest timestamp (a cross-section is one moment).
    top_n=None returns every symbol ranked."""
    files = sorted(glob.glob(os.path.join(directory, "*_cross_section.csv")))
    if not files:
        return {"available": False, "reason": no_files_reason or f"No *_cross_section.csv files found in {directory}"}
    # claude code changed: 2026-10-05 — each symbol's last cs_zscore is read WITH its timestamp. Files end on
    # different dates (measured: 2026-08-19 … 2026-09-27), and a cross-sectional comparison is only meaningful
    # across symbols at the SAME timestamp. The reading uses the most common latest timestamp; symbols whose last
    # value is from another time are excluded from the dispersion and the ranking, and counted.
    latest = {}
    for path in files:
        symbol = os.path.basename(path).replace("_cross_section.csv", "")
        try:
            df = pd.read_csv(path, usecols=["timestamp", "cs_zscore"]).dropna(subset=["cs_zscore"])
            if len(df) > 0:
                latest[symbol] = (pd.Timestamp(df["timestamp"].iloc[-1]), float(df["cs_zscore"].iloc[-1]))
        except Exception:
            continue
    if not latest:
        return {"available": False, "reason": f"No {asset_label} had a readable cs_zscore"}
    counts = pd.Series([ts for ts, _ in latest.values()]).value_counts()
    as_of = max(counts[counts == counts.max()].index)            # most common latest timestamp (latest on a tie)
    latest_zscores = {s: z for s, (ts, z) in latest.items() if ts == as_of}
    if len(latest_zscores) < 3:
        return {"available": False, "reason": f"Fewer than 3 {asset_label} share one latest timestamp ({len(latest_zscores)} at {as_of})"}
    values = list(latest_zscores.values())
    dispersion = float(pd.Series(values).std())
    ranked = sorted(latest_zscores.items(), key=lambda kv: abs(kv[1]), reverse=True)
    return {
        "available": True,
        "dispersion_std": round(dispersion, 4),
        "n_symbols": len(latest_zscores),
        "top_extremes": [{"symbol": s, "cs_zscore": round(z, 3)} for s, z in (ranked if top_n is None else ranked[:top_n])],
        "as_of": as_of.strftime("%Y-%m-%d %H:%M UTC"),             # claude code changed: 2026-10-05
        "n_excluded_other_timestamp": len(latest) - len(latest_zscores),   # claude code changed: 2026-10-05
    }


# ============================================================
# SECTION 2: RESEARCH STATE
# ============================================================
def get_research_state():
    """
    Real, file-based inventory of the offline research pipeline
    (bot/research/*, entirely separate from the live/backtest path —
    see architecture audit's Subsystem A/B split). Nothing here is
    computed live; it's a read of what's already on disk.
    """
    asset_files = glob.glob(os.path.join(DATA_DIR, "*.csv"))
    asset_count = len(asset_files)

    feature_files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv"))
    total_features_evaluated = 0
    total_features_valid = 0
    for path in feature_files:
        try:
            df = pd.read_csv(path, usecols=["valid"])
            total_features_evaluated += len(df)
            total_features_valid += int(df["valid"].sum())
        except Exception:
            continue

    # claude code changed: hardcoded at 2 — verified directly against
    # bot/engines/strategy_router.py, which instantiates exactly
    # MovingAverageStrategy and MeanReversionStrategy as the only two
    # strategies StrategyRouter can route to. grid_strategy.py exists in
    # the repo but is not imported by StrategyRouter (confirmed via grep
    # during the architecture audit) — listed separately as unwired
    # rather than counted as "under evaluation".
    strategies_active = 2
    strategies_unwired = 1  # grid_strategy.py — present, not routed to

    # Latest research run — most recently modified file anywhere under
    # research_data/. Real filesystem mtime, not a claimed schedule.
    latest_mtime, latest_output_file = _latest_research_output()   # claude code changed: 2026-10-05 — outputs only

    if latest_mtime is None:
        latest_run = None
        pipeline_health = "unknown"
        age_days = None
    else:
        latest_run = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - latest_run).days
        pipeline_health = "fresh" if age_days <= RESEARCH_FRESHNESS_DAYS else "stale"

    governance_log_path = os.path.join(RESEARCH_DATA_DIR, "model_governance_log.md")
    governance_entries = None
    if os.path.exists(governance_log_path):
        try:
            with open(governance_log_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Real count of "## " top-level entries (pair verdicts / audit
            # sections) in the governance log — not a parsed structured
            # format, just counting the section markers that already exist
            governance_entries = content.count("\n## ")
        except Exception:
            governance_entries = None

    return {
        "asset_count": asset_count,
        "features_evaluated": total_features_evaluated,
        "features_valid": total_features_valid,
        "feature_files": len(feature_files),
        "strategies_active": strategies_active,
        "strategies_unwired": strategies_unwired,
        "latest_run": latest_run,
        "latest_output_file": latest_output_file,   # claude code changed: 2026-10-05 — which file sets the date
        "age_days": age_days,
        "pipeline_health": pipeline_health,
        "governance_entries": governance_entries,
    }


# ============================================================
# SECTION 3: ALPHA INTELLIGENCE
# ============================================================
def get_alpha_intelligence(cross_section_dispersion, backtest_results):
    """
    Real: top-|IC| features across every research_data/*_validated_features.csv
    (feature predictive power + stability, both already-computed columns
    from bot/research/feature_validator.py — ic_overall and stability_std).
    Cross-sectional opportunities reuse get_cross_sectional_dispersion()'s
    top_extremes. Strategy expectancy comes from the already-cached
    backtest result for the selected symbol.

    "Strongest current signals" is deliberately NOT a ranked live-signal
    list — StrategyRouter produces one signal per regime per symbol, not
    a ranked universe of signals, so there is nothing real to rank here
    beyond what the feature/cross-section tables already show.
    """
    feature_files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv"))
    rows = []
    for path in feature_files:
        symbol = os.path.basename(path).replace("_validated_features.csv", "")
        try:
            df = pd.read_csv(path, usecols=["feature", "ic_overall", "stability_std", "valid", "recommendation"])
            df["symbol"] = symbol
            rows.append(df)
        except Exception:
            continue

    # claude code changed: 2026-10-05 — the date of the validator run behind this table, shown on the panel
    feature_files_date = (datetime.fromtimestamp(max(os.path.getmtime(p) for p in feature_files), tz=timezone.utc)
                          .strftime("%Y-%m-%d") if feature_files else None)
    if rows:
        all_features = pd.concat(rows, ignore_index=True)
        all_features["abs_ic"] = all_features["ic_overall"].abs()
        top_features = all_features.sort_values("abs_ic", ascending=False).head(8)
        top_features_list = [
            {
                "symbol": r["symbol"],
                "feature": r["feature"],
                "ic_overall": round(float(r["ic_overall"]), 4),
                "stability_std": round(float(r["stability_std"]), 4) if pd.notna(r["stability_std"]) else None,
                "valid": bool(r["valid"]),
                "recommendation": r["recommendation"],
            }
            for _, r in top_features.iterrows()
        ]
        feature_power_available = True
    else:
        top_features_list = []
        feature_power_available = False

    expectancy_r = backtest_results.get("avg_r_multiple") if backtest_results else None
    expectancy_dollar = backtest_results.get("expectancy") if backtest_results else None

    return {
        "feature_power_available": feature_power_available,
        "top_features": top_features_list,
        "feature_files_date": feature_files_date,                                      # claude code changed: 2026-10-05
        "n_feature_estimates": int(len(all_features)) if rows else 0,                  # claude code changed: 2026-10-05 — the "of N" for top-8 selection
        "cross_sectional_opportunities": (
            cross_section_dispersion.get("top_extremes", [])
            if cross_section_dispersion.get("available") else []
        ),
        "cross_sectional_available": cross_section_dispersion.get("available", False),
        "strategy_expectancy_r": expectancy_r,
        "strategy_expectancy_dollar": expectancy_dollar,
    }


# ============================================================
# SECTION 4: PORTFOLIO RISK
# ============================================================
def get_portfolio_risk(backtest_results):
    """
    Real where available: max_drawdown comes straight from the already-
    computed backtest result. Correlation is computed here, on demand,
    from the closing prices already stored in data/*.csv (last 500
    candles, returns-based, not raw price correlation) — genuine data,
    not fabricated, but note it's HISTORICAL correlation from the stored
    dataset, not a live cross-asset feed.

    Current exposure and concentration require knowing what's actually
    open right now — see get_execution_state() below, which answers that
    from TradeRecord (the real DB table), not PositionTracker (which only
    exists in a running bot process's memory and isn't reachable from a
    web request — see architecture audit finding on Portfolio/Risk UI).
    Crash-risk state would come from bot/research/contagion_engine.py,
    which exists in the repo but has no persisted output anywhere and is
    not wired into anything (confirmed unwired in the architecture audit)
    — marked unavailable rather than approximated.
    """
    max_drawdown = backtest_results.get("max_drawdown") if backtest_results else None

    correlation = _compute_price_correlation()

    return {
        "max_drawdown": max_drawdown,
        "correlation": correlation,
        "crash_risk": {"available": False, "reason": "contagion_engine.py exists but has no persisted output and is not wired into any pipeline"},
    }


def _compute_price_correlation(window=500, directory=None):
    # claude code changed: new `directory` param — Forex Research
    # Dashboard mission. Default preserves the exact prior behavior
    # (DATA_DIR, i.e. this function's only caller before today,
    # get_portfolio_risk() below, is completely unaffected) so
    # bot/views/forex_terminal_data.py's get_forex_portfolio_risk() can
    # pass DATA_DIR/forex and get genuine Forex cross-pair correlation
    # without a duplicate copy of this function.
    directory = directory if directory is not None else DATA_DIR
    files = sorted(glob.glob(os.path.join(directory, "*.csv")))[:12]  # cap for page-load cost
    if len(files) < 3:
        return {"available": False, "reason": "Fewer than 3 symbol datasets available"}

    closes = {}
    for path in files:
        symbol = os.path.basename(path).replace("_1h.csv", "").replace(".csv", "")   # claude code changed: strip the _1h timeframe suffix too, not just .csv
        try:
            df = pd.read_csv(path)
            time_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
            df[time_col] = pd.to_datetime(df[time_col])
            df = df.set_index(time_col)
            closes[symbol] = df["close"]
        except Exception:
            continue

    if len(closes) < 3:
        return {"available": False, "reason": "Fewer than 3 symbol datasets had a readable close column"}

    # claude code changed: was window-trimming each symbol's series BEFORE
    # aligning them — different symbols' data/*.csv files were fetched at
    # different times and end on different dates (e.g. AAVE runs to
    # 2026-06-30, BTC/DOGE stop at 2025-09-15), so each symbol's
    # independent "last 500 rows" covered a different calendar period and
    # almost nothing overlapped after alignment. Align on the FULL history
    # first (pd.DataFrame outer-joins on the timestamp index), drop rows
    # any symbol is missing, THEN take the most recent overlapping window.
    price_df = pd.DataFrame(closes).dropna()
    if len(price_df) < 30:
        return {"available": False, "reason": "Insufficient overlapping history across symbols"}

    price_df = price_df.tail(window)
    returns = price_df.pct_change().dropna()
    corr = returns.corr()

    # Average pairwise |correlation| — a single real summary number,
    # not an invented "risk score"
    n = len(corr)
    off_diag_sum = corr.abs().values.sum() - n
    avg_abs_corr = off_diag_sum / (n * n - n) if n > 1 else None

    # Highest-correlated pair, for context
    pairs = []
    symbols = list(corr.columns)
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            pairs.append((symbols[i], symbols[j], corr.iloc[i, j]))
    pairs.sort(key=lambda p: abs(p[2]), reverse=True)

    return {
        "available": True,
        "n_symbols": n,
        "window_candles": len(price_df),
        "avg_abs_correlation": round(float(avg_abs_corr), 3) if avg_abs_corr is not None else None,
        "top_pairs": [{"a": a, "b": b, "corr": round(float(c), 3)} for a, b, c in pairs[:5]],
    }


# ============================================================
# SECTION 5: EXECUTION
# ============================================================
def pnl_by_execution_mode(records):   # claude code changed: new — Post-audit Phase 1 journal-mode separation
    """({execution_mode: realised net P&L}, n_unvalued) over closed journal
    rows. Modes are never summed together — LIVE is account P&L, DRY_RUN is
    simulated, UNKNOWN cannot be attributed. A row whose net P&L is None
    (unvalued third-asset fee) is counted in n_unvalued, never added as 0."""
    by_mode, unvalued = {}, 0
    for t in records:
        if t.net_pnl is None:
            unvalued += 1
            continue
        mode = getattr(t, "execution_mode", "") or "UNKNOWN"
        by_mode[mode] = by_mode.get(mode, 0.0) + float(t.net_pnl)
    return {m: round(v, 2) for m, v in sorted(by_mode.items())}, unvalued


def get_execution_state():
    """
    Real TradeRecord query — architecture audit confirmed TradeRecord
    (written by bot.journal.trade_logger.TradeLogger on every live entry/
    exit) was never read by any view. This is a plain read-only query,
    no new write paths.

    Pending orders are marked unavailable: there is no PendingOrder model
    or equivalent anywhere in the schema — OrderManager places orders and
    either confirms a fill or raises, it does not persist an intermediate
    "pending" state anywhere queryable.

    Unrealized P&L on open positions is marked unavailable: it would
    require a live current price per open symbol, which means a live
    exchange call from inside a web request — not implemented here since
    that's a real architectural decision (sync exchange call latency in a
    page load), not a one-line addition.
    """
    from bot.journal.models import MODE_LIVE, TradeRecord   # claude code changed: Post-audit Phase 1 — MODE_LIVE

    try:
        open_positions = list(
            TradeRecord.objects.filter(status="OPEN").order_by("-created_at")[:50]
        )
        closed_trades = list(
            TradeRecord.objects.exclude(status="OPEN").order_by("-created_at")[:25]
        )
        total_closed = TradeRecord.objects.exclude(status="OPEN").count()

        # claude code changed: Post-audit Phase 1 journal-mode separation — the
        # headline realised P&L / fees / slippage are LIVE rows only (was: every
        # row, so dry-run simulated P&L was shown as "Realized P&L"); every mode
        # is still reported, separately, in realized_pnl_by_mode. A row with
        # unvalued net P&L is counted, never added as 0.
        closed_all = TradeRecord.objects.exclude(status="OPEN")
        realized_pnl_by_mode, unvalued_pnl_count = pnl_by_execution_mode(closed_all)
        realized_pnl = 0.0
        total_fees = 0.0
        total_slippage_events = 0
        slippage_sum = 0.0
        for t in closed_all.filter(execution_mode=MODE_LIVE):
            realized_pnl += t.net_pnl or 0.0   # None rows are excluded from the headline below via unvalued_pnl_count
            total_fees += (t.fee_entry or 0.0) + (t.fee_exit or 0.0)
            if t.slippage_in:
                slippage_sum += abs(t.slippage_in)
                total_slippage_events += 1
            if t.slippage_out:
                slippage_sum += abs(t.slippage_out)
                total_slippage_events += 1

        avg_slippage_pct = (slippage_sum / total_slippage_events) if total_slippage_events else None

        return {
            "available": True,
            "open_positions": open_positions,
            "open_count": len(open_positions),
            "recent_closed": closed_trades,
            "total_closed": total_closed,
            "realized_pnl": round(realized_pnl, 2),
            "realized_pnl_scope": "LIVE trades only",            # claude code changed: Post-audit Phase 1 journal-mode separation
            "realized_pnl_by_mode": realized_pnl_by_mode,        # claude code changed: e.g. {"DRY_RUN": -0.12} — simulated, never account P&L
            "unvalued_pnl_count": unvalued_pnl_count,            # claude code changed: rows whose net P&L could not be valued (third-asset fee)
            "total_fees": round(total_fees, 2),
            "avg_slippage_pct": round(avg_slippage_pct, 4) if avg_slippage_pct is not None else None,
            "unrealized_pnl": {"available": False, "reason": "Requires a live price fetch per open symbol inside the request — not implemented"},
            "pending_orders": {"available": False, "reason": "No pending-order state is persisted anywhere; OrderManager either confirms a fill or raises"},
        }
    except Exception as e:
        return {"available": False, "reason": f"TradeRecord query failed: {e}"}


# ============================================================
# SECTION 6: SYSTEM HEALTH
# ============================================================
def get_system_health():
    """
    Lightweight, request-safe checks — deliberately NOT calling
    bot.management.commands.deep_health_check's Command.handle(), which
    builds synthetic test data and runs structural/functional/behavioural/
    integration/drift layers meant for a CLI run, not a page load. This
    checks: DB connectivity (real query), whether each engine's modules
    actually import (real — this is the same import chain that was
    broken pre-fix for market_data.py/execution_engine.py), research data
    freshness (reused from get_research_state), and whether exchange API
    credentials are configured (checks the env var only — does not make
    a network call from inside a page load).

    "Execution Engine: OK" here means the code imports cleanly, NOT that
    a live bot process is currently running — bot_runner.py is a separate
    process this web request has no visibility into. Labelled accordingly.
    """
    checks = []

    # ---- Database ----
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks.append({"name": "Database", "status": "ok", "detail": connection.settings_dict.get("ENGINE", "").rsplit(".", 1)[-1]})
    except Exception as e:
        checks.append({"name": "Database", "status": "err", "detail": str(e)[:80]})

    # ---- Market data ----
    checks.append(_import_check("Market Data Engine", "bot.engines.market_data", "MarketData"))
    checks.append(_import_check("Market Data Fetcher", "bot.data_fetcher", "get_klines"))

    # ---- Strategy engine ----
    checks.append(_import_check("Regime Detector", "bot.engines.regime_detector", "RegimeDetector"))
    checks.append(_import_check("Strategy Router", "bot.engines.strategy_router", "StrategyRouter"))

    # ---- Risk engine ----
    checks.append(_import_check("Risk / Position Sizer", "bot.risk.position_sizer", "PositionSizer"))

    # ---- Execution engine ----
    checks.append(_import_check("Execution Engine (code)", "bot.engines.execution_engine", "ExecutionEngine"))

    # ---- Research engine (freshness, not import) ----
    latest, latest_name = _latest_research_output()   # claude code changed: 2026-10-05 — outputs only (documents excluded)
    if latest is not None:
        age_days = (time.time() - latest) / 86400
        status = "ok" if age_days <= RESEARCH_FRESHNESS_DAYS else "warn"
        checks.append({"name": "Research Engine", "status": status, "detail": f"last output {age_days:.1f}d ago ({latest_name})"})
    else:
        checks.append({"name": "Research Engine", "status": "err", "detail": "no research_data/ output found"})

    # ---- API / exchange credentials ----
    has_key = bool(os.getenv("BINANCE_API_KEY")) and os.getenv("BINANCE_API_KEY") != "YOUR_API_KEY_HERE"
    checks.append({
        "name": "Exchange API Credentials",
        "status": "ok" if has_key else "warn",
        "detail": "configured" if has_key else "not configured (BINANCE_API_KEY unset)",
    })

    n_err = sum(1 for c in checks if c["status"] == "err")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    overall = "err" if n_err else ("warn" if n_warn else "ok")

    return {"checks": checks, "overall": overall}


def _import_check(name, module_path, attr):
    try:
        mod = importlib.import_module(module_path)
        getattr(mod, attr)
        return {"name": name, "status": "ok", "detail": "import OK"}
    except Exception as e:
        return {"name": name, "status": "err", "detail": str(e)[:80]}
