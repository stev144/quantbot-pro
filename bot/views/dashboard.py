from django.shortcuts import render
from django.http import JsonResponse
from django.core.cache import cache

from bot.data_fetcher import get_klines
from bot.backtesting.backtester import backtest
from bot.engines.strategy_scorer import StrategyScorer
from bot.engines.regime_detector import RegimeDetector

# claude code changed: new — real data sources for the institutional-
# terminal dashboard redesign (market state / research state / alpha
# intelligence / portfolio risk / execution / system health). See
# bot/views/terminal_data.py's module docstring for the "no fabricated
# metrics" rationale behind each function.
from bot.views import terminal_data

import os
import ccxt

try:
    from bot.backtesting.portfolio_backtester import PortfolioBacktester
except Exception:
    PortfolioBacktester = None

def dashboard(request):
    # ==============================
    # SYMBOL
    # ==============================
    symbol = request.GET.get("symbol", "AVAXUSDT").upper()

    cache_key = f"backtest_results_{symbol}"
    results = cache.get(cache_key)

    # claude code changed: new — regime_result is computed fresh below
    # (outside the results cache) even on a cache hit, since it reflects
    # the CURRENT market state for the Market State panel, not a
    # snapshot from whenever the backtest was originally cached.
    # ==============================
    # FETCH + BACKTEST
    # ==============================
    if results is None:
        df = get_klines(symbol, interval="1h", total_candles=10000)

        if df is None or df.empty:
            return render(request, "dashboard.html", {
                "error": "Failed to fetch market data"
            })

        results = backtest(df) or {}
        cache.set(cache_key, results, 3600)
    else:
        # claude code changed: new — cache hit still needs current-state
        # data for the Market State panel below, so fetch klines even
        # though the backtest itself came from cache
        df = get_klines(symbol, interval="1h", total_candles=10000)

    # claude code changed: new — current regime for the Market State
    # panel, computed on the same df already fetched above (no extra
    # network/exchange call). Same RegimeDetector class the live bot and
    # backtester both use (bot/engines/regime_detector.py) — real,
    # shared logic, not a redesign-specific approximation.
    if df is not None and not df.empty:
        current_regime_result = RegimeDetector().detect(df)
    else:
        current_regime_result = RegimeDetector().detect(None)  # returns the safe RANGING/LOW-confidence default

    # ==============================
    # NORMALIZE TRADES
    # ==============================
    raw_trades = results.get("trades", [])
    trades = []

    # claude code changed: was recomputing equity/drawdown here from raw
    # trade P&L with a peak starting at 0 — whenever cumulative P&L never
    # went positive (or only briefly did, near the start), `peak` stayed
    # at (or returned to) 0 and the `dd = 0 if peak == 0 else ...` guard
    # silently reported 0% drawdown for that entire stretch, real losses
    # or not. Confirmed on real symbols: DOGEUSDT's cumulative P&L never
    # went positive across 318 trades (ended near -$3,499) and every
    # single drawdown point rendered as exactly 0 — a flat line hiding
    # the whole loss; BTCUSDT touched a small early peak around trade 4
    # then declined for the rest of the run, producing a flat segment
    # then a long, roughly-straight climb from that one peak — exactly
    # the "straight line from the middle" shape being reported.
    # bot/backtesting/backtester.py's Backtester already tracks a correct
    # equity_curve/drawdown_curve internally, with peak_balance seeded at
    # the real initial_balance (never 0, so no div-by-zero/masking edge
    # case) — use that directly instead of re-deriving a buggier copy.
    equity_curve = results.get("equity_curve", [])
    drawdown_curve = results.get("drawdown_curve", [])

    for t in raw_trades:
        trade = {
            "direction": t.get("direction", ""),
            "entry_price": _safe_float(t.get("entry_price")),
            "exit_price": _safe_float(t.get("exit_price")),
            "sl": _safe_float(t.get("sl")),
            "tp": _safe_float(t.get("tp")),
            "profit": _safe_float(t.get("net_pnl") or t.get("profit")),
            "r_multiple": _safe_float(t.get("r_multiple")),
            "holding_candles": t.get("holding_candles") or 0,
            "exit_reason": t.get("exit_reason") or t.get("reason") or "-",
            "reason": t.get("reason") or "No narrative",
            "entry_narrative": t.get("entry_narrative", ""),
            "exit_narrative": t.get("exit_narrative", ""),
            "research_verdict": t.get("research_verdict", ""),        # claude code changed: new — Phase 3 (UI pipeline view)
            "production_eligible": t.get("production_eligible", False),  # claude code changed: new
        }
        trades.append(trade)

    # ==============================
    # CORE METRICS
    # ==============================
    total_trades = results.get("total_trades", 0)
    wins = results.get("wins", 0)
    losses = total_trades - wins

    win_rate = _safe_float(results.get("win_rate"))
    expectancy = _safe_float(results.get("expectancy"))
    profit_factor = _safe_float(results.get("profit_factor"))
    avg_r = _safe_float(results.get("avg_r_multiple"))

    sharpe = _safe_float(results.get("sharpe_ratio"))
    sortino = _safe_float(results.get("sortino_ratio"))
    max_drawdown = _safe_float(results.get("max_drawdown"))
    loss_streak = results.get("max_consecutive_losses", 0)

    # ==============================
    # REGIME + STRUCTURE
    # ==============================
    regime_breakdown = results.get("regime_breakdown", {})
    structure_breakdown = results.get("structure_breakdown", {})

    # ==============================
    # STRATEGY SCORING
    # ==============================
    scorer = StrategyScorer(results)
    score_result = scorer.evaluate()

    total_score = score_result.get("total_score", 0)
    grade = score_result.get("grade", "F")
    verdict = score_result.get("verdict", "Unknown")
    validation_penalty = score_result.get("validation_penalty", 0)
    suggestions = score_result.get("suggestions", [])
    regime_notes = score_result.get("regime_notes", {})
    structure_notes = score_result.get("structure_notes", {})

    # claude code changed: new — computed here instead of via the template's
    # "|lower|slugify" chain. Django's slugify does NOT convert underscores to
    # hyphens (only whitespace/repeated hyphens get collapsed), so "TRENDING_UP"
    # slugified to "trending_up" — but the CSS only defines .regime-trending-up
    # (hyphen). RANGING/UNKNOWN have no underscore so they slugged fine and looked
    # correctly styled, which is exactly why only the trending/high-volatility
    # states looked stuck on default (ranging-like) styling on the pulse card.
    dominant_regime_slug = str(
        regime_notes.get("dominant_regime", "UNKNOWN")
    ).lower().replace("_", "-")

    # claude code changed: removed the ad hoc "INSIGHTS ENGINE" block that used to live
    # here. Every check in it duplicated (with cruder thresholds and no sample-size
    # awareness) logic StrategyScorer._get_suggestions() already runs: profit_factor<1.5
    # and total_trades<50 are literally PROFIT_FACTOR_GOOD/TRADES_GOOD in strategy_scorer.py;
    # the dollar-based "expectancy<=0" check is a weaker version of the R-based,
    # asset-agnostic EXPECTANCY_GOOD check there; and "Disable SHORTS" on any negative
    # TRENDING_DOWN profit (no matter how few trades) duplicated — and was less careful
    # than — _build_structure_notes()'s directional_note, which now has a proper
    # TRADES_MARGINAL sample-size gate before making that same recommendation. Keeping
    # both produced duplicate/contradictory-sounding suggestions on the same page.
    # StrategyScorer is now the single source of truth for all AI Insights text.

    # claude code changed: new — the six terminal-dashboard sections.
    # Each terminal_data function either returns real computed data or an
    # explicit {"available": False, "reason": ...} marker; nothing here
    # is a placeholder number. See terminal_data.py for what each one
    # reads and why.
    # ==============================
    # TERMINAL SECTIONS
    # ==============================
    cross_section_dispersion = terminal_data.get_cross_sectional_dispersion()
    market_state = terminal_data.get_market_state(current_regime_result, cross_section_dispersion)
    research_state = terminal_data.get_research_state()
    alpha_intelligence = terminal_data.get_alpha_intelligence(cross_section_dispersion, results)
    portfolio_risk = terminal_data.get_portfolio_risk(results)
    execution_state = terminal_data.get_execution_state()
    system_health = terminal_data.get_system_health()

    # ==============================
    # FINAL CONTEXT
    # ==============================
    context = {
        "symbol": symbol,

        # Terminal sections
        "market_state": market_state,
        "research_state": research_state,
        "alpha_intelligence": alpha_intelligence,
        "portfolio_risk": portfolio_risk,
        "execution_state": execution_state,
        "system_health": system_health,

        # Strategy health
        "validation_penalty": validation_penalty,
        "total_score": total_score,
        "grade": grade,
        "verdict": verdict,

        # Metrics
        "total_trades": total_trades,  # claude code changed: new — was computed above but never added to context, so template always showed the |default:'0' fallback
        "wins": wins,                  # claude code changed: new — same bug as total_trades
        "losses": losses,              # claude code changed: new — same bug as total_trades
        "win_rate": win_rate,
        "expectancy_r": expectancy,
        "profit_factor": profit_factor,
        "avg_r": avg_r,
        "max_drawdown": max_drawdown,
        "loss_streak": loss_streak,
        "sharpe": sharpe,
        "sortino": sortino,

        # Intelligence
        "regime_breakdown": regime_breakdown,
        "structure_breakdown": structure_breakdown,
        "regime_notes": regime_notes,
        "structure_notes": structure_notes,
        "dominant_regime_slug": dominant_regime_slug,  # claude code changed: new — see computation above

        # Suggestions
        "suggestions": suggestions,

        # Charts
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,

        # Trades
        "trades": trades,
        "rejection_table": results.get("rejection_table", {}),
        "rejection_insights": results.get("rejection_insights", {}),

        # Misc
        "error": None,
    }

    return render(request, "dashboard.html", context)

def live_data(request):
    import logging
    logger = logging.getLogger(__name__)
    logger.info("=== LIVE DATA VIEW HIT ===")

    try:
        symbol = request.GET.get("symbol", "BTCUSDT").upper()
        # claude code changed: was reusing dashboard()'s cache key
        # (f"backtest_results_{symbol}") but writing a differently-shaped
        # count-up-widget payload back into it below at a 60s TTL — for up
        # to 60s after this view ran, dashboard()'s cache hit on the same
        # symbol would silently get this shape instead of the raw
        # backtest results dict, and results.get("trades", []) would
        # return [] with no error. Distinct key so the two views stop
        # colliding.
        backtest_cache_key = f"backtest_results_{symbol}"
        live_cache_key = f"live_data_{symbol}"
        results = cache.get(backtest_cache_key)

        if results is None:
            df = get_klines(symbol, interval="1h", total_candles=10000)
            if df is None or df.empty:
                return JsonResponse({"success": False, "summary": {}})
            results = backtest(df) or {}
            cache.set(backtest_cache_key, results, 3600)

        try:
            # claude code changed: was `from bot.analytics import
            # StrategyScorer` — bot/analytics.py only defines
            # TradeAnalytics (an older, separate implementation from
            # bot/engines/analytics_engine.py's), it has never defined
            # StrategyScorer. That import always raised ImportError, was
            # silently caught by the except below, and total_score/grade
            # in every response from this view were permanently the
            # fallback 0/"—" regardless of actual backtest performance.
            from bot.engines.strategy_scorer import StrategyScorer
            score_result = StrategyScorer(results).evaluate()
            total_score  = round(score_result.get("total_score", 0), 1)
            grade        = score_result.get("grade", "—")
        except Exception as score_err:
            logger.error(f"StrategyScorer failed: {score_err}")
            total_score = 0
            grade       = "—"

        total_trades = results.get("total_trades", 0)
        wins         = results.get("wins", 0)
        
        payload = {
            "success": True,
            "timestamp": "live",
            "summary": {
                "total_trades": {
                    "value": total_trades,
                    "duration": 800,
                    "decimals": 0,
                },
                "wins": {
                    "value": wins,
                    "duration": 800,
                    "decimals": 0,
                },
                "losses": {
                    "value": total_trades - wins,
                    "duration": 800,
                    "decimals": 0,
                },
                "win_rate": {
                    "value": round(results.get("win_rate", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "expectancy_r": {
                    "value": round(results.get("expectancy", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "profit_factor": {
                    "value": round(results.get("profit_factor", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "avg_r": {
                    "value": round(results.get("avg_r_multiple", 0), 4),
                    "duration": 1200,
                    "decimals": 4,
                },
                "max_drawdown": {
                    "value": round(results.get("max_drawdown", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "loss_streak": {
                    "value": results.get("max_consecutive_losses", 0),
                    "duration": 800,
                    "decimals": 0,
                },
                "sharpe": {
                    "value": round(results.get("sharpe_ratio", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "sortino": {
                    "value": round(results.get("sortino_ratio", 0), 2),
                    "duration": 1200,
                    "decimals": 2,
                },
                "total_score": {
                    "value": total_score,
                    "duration": 1500,
                    "decimals": 1,
                },
                "grade": {
                    "value": grade,
                    "duration": 0,
                    "decimals": 0,
                },
            }
        }


        cache.set(live_cache_key, payload, 60)
        logger.info(f"=== LIVE DATA SUCCESS for {symbol} ===")
        return JsonResponse(payload)

    except Exception as e:
        logger.error(f"=== LIVE DATA CRASHED: {e} ===", exc_info=True)
        return JsonResponse({
            "success": False,
            "error":   str(e),
            "summary": {}
        }, status=200)

def portfolio_backtest_view(request):
    # Accept pairs from query string e.g. ?pairs=BTCUSDT,ETHUSDT
    pairs_param = request.GET.get("pairs", "")

    if pairs_param:
        raw = [p.strip().upper() for p in pairs_param.split(",") if p.strip()]
        # Convert BTCUSDT → BTC/USDT format
        def to_slash(symbol):
            for quote in ["USDT", "BTC", "ETH", "BNB"]:
                if symbol.endswith(quote) and symbol != quote:
                    base = symbol[:-len(quote)]
                    return base + "/" + quote
            return symbol
        symbols = [to_slash(s) for s in raw]
    else:
        symbols = None  # use defaults

    if PortfolioBacktester is None:
        return JsonResponse({
            "success": False,
            "error": "PortfolioBacktester is not available"
        })

    try:
        exchange = ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY"),
            "secret": os.getenv("BINANCE_API_SECRET"),
        })
        exchange.load_markets()

        portfolio = PortfolioBacktester(
            exchange=exchange,
            symbols=symbols,
            candle_limit=2000,
            initial_balance=5000,
        )

        results = portfolio.run()
        portfolio.print_report()

        return JsonResponse(results, safe=False)

    except Exception as e:
        import traceback
        return JsonResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        })
def _safe_float(value):
    if value in (None, "", "None"):
        return 0
    try:
        return round(float(value), 6)
    except:
        return 0
    
def live_regime_monitor(request):
    """
    Returns both the 60-day dominant regime and the current live regime
    for any given symbol. Used by the Live Regime Monitor on the dashboard.
    """
    import traceback
    from bot.data_fetcher import get_klines
    from bot.engines.regime_detector import RegimeDetector

    symbol = request.GET.get("symbol", "BTCUSDT").upper()

    try:
        detector = RegimeDetector()

        # ── 60-DAY DOMINANT REGIME ────────────────────
        # 1440 candles on 1H = exactly 60 days
        df_60 = get_klines(symbol, interval="1h", total_candles=1440)

        if df_60 is None or df_60.empty:
            return JsonResponse({
                "success": False,
                "error": f"No data returned for {symbol}"
            })

        # Run regime detection on every 50-candle window
        # to build a regime distribution over 60 days
        regime_counts = {
            "TRENDING_UP":    0,
            "TRENDING_DOWN":  0,
            "RANGING":        0,
            "HIGH_VOLATILITY": 0,
            "UNKNOWN":        0,
        }

        window_size = 100
        step        = 24
        total_windows = 0

        for start in range(0, len(df_60) - window_size, step):
            window = df_60.iloc[start:start + window_size]
            try:
                result = detector.detect(window)
                r = result.regime if result.regime in regime_counts else "UNKNOWN"
                regime_counts[r] += 1
                total_windows += 1
            except Exception:
                continue

        # Calculate percentages
        regime_pcts = {}
        dominant_regime = "UNKNOWN"
        dominant_pct    = 0

        for regime, count in regime_counts.items():
            pct = round((count / total_windows * 100), 1) if total_windows > 0 else 0
            regime_pcts[regime] = pct
            if pct > dominant_pct:
                dominant_pct    = pct
                dominant_regime = regime

        # ── CURRENT LIVE REGIME ───────────────────────
        # Last 100 candles = last ~4 days on 1H
        # Enough for indicators to warm up properly
        df_live = df_60.iloc[-100:]

        live_result = detector.detect(df_live)

        current_regime     = live_result.regime
        current_confidence = live_result.confidence
        current_adx        = live_result.adx
        current_atr_ratio  = live_result.atr_ratio
        current_summary    = live_result.summary

        # ── ALIGNMENT INTELLIGENCE ────────────────────
        def get_alignment(dominant, current):
            if dominant == current:
                if dominant in ("TRENDING_UP", "TRENDING_DOWN"):
                    return {
                        "status":  "ALIGNED",
                        "icon":    "✅",
                        "color":   "#00c87a",
                        "message": (
                            f"Current {current.replace('_', ' ').title()} aligns with "
                            f"the 60-day dominant regime. Conditions are favourable. "
                            f"Strategy has historical precedent here."
                        )
                    }
                elif dominant == "RANGING":
                    return {
                        "status":  "ALIGNED",
                        "icon":    "✅",
                        "color":   "#f0b90b",
                        "message": (
                            "Market is ranging both historically and currently. "
                            "Mean reversion strategies are favoured. "
                            "Avoid breakout entries."
                        )
                    }
                else:
                    return {
                        "status":  "CAUTION",
                        "icon":    "⚠️",
                        "color":   "#ff8c42",
                        "message": (
                            "High volatility detected both historically and currently. "
                            "Sit out or reduce position size significantly."
                        )
                    }

            # Misaligned regimes
            if dominant == "RANGING" and current in ("TRENDING_UP", "TRENDING_DOWN"):
                return {
                    "status":  "CAUTION",
                    "icon":    "⚠️",
                    "color":   "#ff8c42",
                    "message": (
                        f"Market historically ranges {dominant_pct}% of the time "
                        f"but is currently {current.replace('_', ' ').title()}. "
                        f"This trend may be a temporary breakout. "
                        f"Trade with reduced size and tighter stops."
                    )
                }

            if dominant in ("TRENDING_UP", "TRENDING_DOWN") and current == "RANGING":
                return {
                    "status":  "CAUTION",
                    "icon":    "⚠️",
                    "color":   "#ff8c42",
                    "message": (
                        f"Market has been trending historically but is currently ranging. "
                        f"Trend may be pausing or reversing. "
                        f"Wait for trend confirmation before entering."
                    )
                }

            if dominant == "TRENDING_UP" and current == "TRENDING_DOWN":
                return {
                    "status":  "WARNING",
                    "icon":    "🔴",
                    "color":   "#ff4d4d",
                    "message": (
                        "REVERSAL SIGNAL — Market was historically bullish but is now "
                        "trending down. Possible trend reversal in progress. "
                        "Avoid longs. Watch for confirmation of new downtrend."
                    )
                }

            if dominant == "TRENDING_DOWN" and current == "TRENDING_UP":
                return {
                    "status":  "WARNING",
                    "icon":    "🟡",
                    "color":   "#f0b90b",
                    "message": (
                        "POTENTIAL REVERSAL — Market was historically bearish but is now "
                        "showing upward momentum. Could be a relief rally or genuine reversal. "
                        "Trade cautiously with confirmation."
                    )
                }

            if current == "HIGH_VOLATILITY":
                return {
                    "status":  "WARNING",
                    "icon":    "🔴",
                    "color":   "#ff4d4d",
                    "message": (
                        "Current high volatility overrides historical regime. "
                        "Risk is elevated. Reduce size or sit out entirely "
                        "until volatility normalises."
                    )
                }

            return {
                "status":  "NEUTRAL",
                "icon":    "◌",
                "color":   "#888",
                "message": "Regime data is insufficient for a clear alignment signal."
            }

        alignment = get_alignment(dominant_regime, current_regime)

        return JsonResponse({
            "success": True,
            "symbol":  symbol,
            "historical": {
                "dominant_regime": dominant_regime,
                "dominant_pct":    dominant_pct,
                "distribution":    regime_pcts,
                "windows_analysed": total_windows,
                "period":          "60 days",
            },
            "current": {
                "regime":     current_regime,
                "confidence": current_confidence,
                "adx":        current_adx,
                "atr_ratio":  current_atr_ratio,
                "summary":    current_summary,
            },
            "alignment": alignment,
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error":   str(e),
            "trace":   traceback.format_exc()
        })   
        
def walk_forward_view(request):
    """
    Splits data into in-sample (70%) and out-of-sample (30%)
    and runs backtest on both. Returns comparison metrics.
    """
    symbol = request.GET.get("symbol", "BTCUSDT").upper()
    split = float(request.GET.get("split", 0.7))  # default 70/30
    split = max(0.5, min(0.85, split))  # clamp between 0.5 and 0.85

    result = run_walk_forward_validation(symbol, split)
    return JsonResponse(result)


def run_walk_forward_validation(symbol, split):
    """
    claude code changed: extracted from walk_forward_view's body so
    bot/views/backtesting_data.py (Backtesting section) can call the same
    real, already-proven computation directly instead of duplicating it
    or reaching into another view function. walk_forward_view above is
    now a thin request-parsing wrapper around this.
    """
    import traceback
    from bot.data_fetcher import get_klines
    from bot.backtesting.backtester import backtest

    try:
        # Fetch 1440 candles = 60 days on 1H
        df = get_klines(symbol, interval="1h", total_candles=1440)

        if df is None or df.empty:
            return {
                "success": False,
                "error": f"No data returned for {symbol}"
            }

        if len(df) < 200:
            return {
                "success": False,
                "error": f"Insufficient data: {len(df)} candles. Need at least 200."
            }

        # ── SPLIT ─────────────────────────────────────
        split_idx   = int(len(df) * split)
        df_in       = df.iloc[:split_idx]
        df_out      = df.iloc[split_idx:]

        in_candles  = len(df_in)
        out_candles = len(df_out)
        in_days     = round(in_candles / 24, 1)
        out_days    = round(out_candles / 24, 1)

        # ── RUN BOTH BACKTESTS ─────────────────────────
        results_in  = backtest(df_in)  or {}
        results_out = backtest(df_out) or {}

        def safe(d, key, decimals=2):
            try:
                return round(float(d.get(key, 0) or 0), decimals)
            except Exception:
                return 0

        # ── BUILD METRIC COMPARISON ────────────────────
        def build_metrics(r):
            return {
                "total_trades":    safe(r, "total_trades", 0),
                "win_rate":        safe(r, "win_rate"),
                "profit_factor":   safe(r, "profit_factor"),
                "expectancy_r":    safe(r, "expectancy_r"),
                "avg_r":           safe(r, "avg_r_multiple", 4),
                "max_drawdown":    safe(r, "max_drawdown"),
                "sharpe":          safe(r, "sharpe_ratio"),
                "sortino":         safe(r, "sortino_ratio"),
                "loss_streak":     safe(r, "max_consecutive_losses", 0),
                "final_balance":   safe(r, "final_balance"),
                "total_fees":      safe(r, "total_fees_paid"),
            }

        in_metrics  = build_metrics(results_in)
        out_metrics = build_metrics(results_out)

        # ── DEGRADATION ANALYSIS ───────────────────────
        # For each metric calculate how much it degraded
        # positive degradation = got worse out-of-sample
        def degradation(metric, in_val, out_val, higher_is_better=True):
            if in_val == 0:
                return 0
            change_pct = ((out_val - in_val) / abs(in_val)) * 100
            if higher_is_better:
                return round(change_pct, 1)   # negative = degraded
            else:
                return round(-change_pct, 1)  # for drawdown, higher is worse

        degradations = {
            "win_rate":      degradation("win_rate",      in_metrics["win_rate"],      out_metrics["win_rate"]),
            "profit_factor": degradation("profit_factor", in_metrics["profit_factor"], out_metrics["profit_factor"]),
            "expectancy_r":  degradation("expectancy_r",  in_metrics["expectancy_r"],  out_metrics["expectancy_r"]),
            "max_drawdown":  degradation("max_drawdown",  in_metrics["max_drawdown"],  out_metrics["max_drawdown"], higher_is_better=False),
            "sharpe":        degradation("sharpe",        in_metrics["sharpe"],        out_metrics["sharpe"]),
        }

        # ── FINAL VERDICT ──────────────────────────────
        # Count how many key metrics held up out-of-sample
        key_metrics  = ["win_rate", "profit_factor", "expectancy_r", "sharpe"]
        held         = sum(1 for m in key_metrics if degradations[m] >= -20)
        failed       = sum(1 for m in key_metrics if degradations[m] < -50)
        avg_degrade  = round(
            sum(degradations[m] for m in key_metrics) / len(key_metrics), 1
        )

        if out_metrics["total_trades"] < 5:
            verdict = {
                "status":  "INSUFFICIENT",
                "icon":    "⚠️",
                "color":   "#888",
                "grade":   "N/A",
                "message": (
                    f"Only {out_metrics['total_trades']} trades in the out-of-sample "
                    f"period. Need at least 100+ trades for a meaningful validation. "
                    f"Try a longer dataset or different pair."
                )
            }
        elif failed >= 2:
            verdict = {
                "status":  "FAILED",
                "icon":    "🔴",
                "color":   "#ff4d4d",
                "grade":   "F",
                "message": (
                    f"{failed} key metrics collapsed by more than 50% out-of-sample. "
                    f"This strategy is likely overfit to the in-sample data. "
                    f"Do NOT deploy live. Review your entry conditions and reduce complexity."
                )
            }
        elif held >= 3 and avg_degrade >= -15:
            verdict = {
                "status":  "VALIDATED",
                "icon":    "✅",
                "color":   "#00c87a",
                "grade":   "A",
                "message": (
                    f"{held}/4 key metrics held within acceptable range out-of-sample. "
                    f"Average degradation: {abs(avg_degrade)}%. "
                    f"Strategy shows genuine robustness. "
                    f"Proceed to paper trading with confidence."
                )
            }
        elif held >= 2 and avg_degrade >= -30:
            verdict = {
                "status":  "ACCEPTABLE",
                "icon":    "🟡",
                "color":   "#f0b90b",
                "grade":   "B",
                "message": (
                    f"{held}/4 key metrics held out-of-sample. "
                    f"Average degradation: {abs(avg_degrade)}%. "
                    f"Strategy shows moderate robustness. "
                    f"Paper trade for at least 2 weeks before going live."
                )
            }
        else:
            verdict = {
                "status":  "DEGRADED",
                "icon":    "🟠",
                "color":   "#ff8c42",
                "grade":   "C",
                "message": (
                    f"Strategy degrades significantly out-of-sample. "
                    f"Average degradation: {abs(avg_degrade)}%."
                    f"Review and simplify entry conditions. "
                    f"Consider more data before deployment."
                )
            }

        return {
            "success":     True,
            "symbol":      symbol,
            "split_pct":   round(split * 100),
            "in_sample": {
                "candles": in_candles,
                "days":    in_days,
                "metrics": in_metrics,
            },
            "out_sample": {
                "candles": out_candles,
                "days":    out_days,
                "metrics": out_metrics,
            },
            "degradations": degradations,
            "verdict":      verdict,
        }

    except Exception as e:
        return {
            "success": False,
            "error":   str(e),
            "trace":   traceback.format_exc()
        }

        