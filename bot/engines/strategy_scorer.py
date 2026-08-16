# ============================================================
# bot/engines/strategy_scorer.py
# Strategy Scorer — evaluates backtest quality with full
# regime awareness, structure awareness, walk-forward split,
# and sample-size reliability discounting.
#
# REFINEMENTS FROM PREVIOUS VERSION:
# 1. Dollar expectancy replaced with R-based expectancy
#    — asset-agnostic: works the same on BTC and NEAR
#    — EXPECTANCY_EXCELLENT = 0.5R per trade (not $50)
#
# 2. Structure breakdown integrated
#    — reads structure_breakdown from backtester results
#    — flags when UPTREND trades lose and DOWNTREND trades win
#    — adds structure notes to the score report
#
# 3. Walk-forward awareness added
#    — splits trades into first half and second half
#    — compares performance between halves
#    — warns if strategy is degrading over time (overfitting signal)
#
# 4. Regime notes enhanced
#    — now flags the specific regime to avoid
#    — suggests blocking the worst regime in router settings
#
# 5. Score report expanded
#    — structure_notes added to return dict
#    — walk_forward_notes added to return dict
#    — all existing keys preserved for dashboard compatibility
# ============================================================

from typing import Any, Dict, List    # Type hints for clarity
import random                          # Used by robustness tester


class StrategyScorer:
    """
    Scores a backtest result across six dimensions:
    1. Profitability  — expectancy (R-based) + profit factor
    2. Consistency    — win rate + average R
    3. Risk control   — max drawdown + loss streak
    4. Edge quality   — Sharpe + Sortino
    5. Cost efficiency — fees + slippage vs gross profit
    6. Sample size    — trade count reliability weighting

    All scores are multiplied by a reliability factor
    based on sample size — small samples are discounted
    so they cannot produce misleadingly high scores.

    NEW in this version:
    - Structure breakdown analysis
    - Walk-forward split (first half vs second half)
    - R-based expectancy thresholds (asset-agnostic)
    - True rolling walk-forward test
    - Robustness test via trade order perturbation
    """

    # ── PROFITABILITY THRESHOLDS — R-BASED ───────────────────
    # Changed from dollar amounts to R multiples
    # R-based thresholds work for any asset at any price level
    # BTC at $60,000 and NEAR at $5 now use the same scale
    EXPECTANCY_EXCELLENT = 0.5    # +0.5R per trade = strong edge
    EXPECTANCY_GOOD      = 0.2    # +0.2R per trade = viable edge
    EXPECTANCY_MARGINAL  = 0.0    # 0R = breakeven = no edge yet

    PROFIT_FACTOR_EXCELLENT = 2.0    # Gross wins double gross losses
    PROFIT_FACTOR_GOOD      = 1.5    # Target for institutional grade
    PROFIT_FACTOR_MARGINAL  = 1.0    # Breakeven — barely covering losses

    # ── CONSISTENCY THRESHOLDS ───────────────────────────────
    WIN_RATE_EXCELLENT = 60.0    # 6 in 10 trades win
    WIN_RATE_GOOD      = 50.0    # 5 in 10 trades win
    WIN_RATE_MARGINAL  = 35.0    # Viable only with high RRR

    AVG_R_EXCELLENT = 1.0    # Each trade averages 1R profit
    AVG_R_GOOD      = 0.5    # Each trade averages 0.5R profit
    AVG_R_MARGINAL  = 0.0    # Breakeven on average

    # ── RISK CONTROL THRESHOLDS ──────────────────────────────
    DRAWDOWN_EXCELLENT = 5.0     # Max 5% peak-to-trough
    DRAWDOWN_GOOD      = 15.0    # Max 15% — acceptable
    DRAWDOWN_MARGINAL  = 25.0    # Max 25% — borderline dangerous

    LOSS_STREAK_EXCELLENT = 3    # 3 or fewer consecutive losses
    LOSS_STREAK_GOOD      = 5    # 5 or fewer
    LOSS_STREAK_MARGINAL  = 8    # 8 or fewer — borderline

    # ── EDGE QUALITY THRESHOLDS ──────────────────────────────
    SHARPE_EXCELLENT = 2.0    # Strong risk-adjusted return
    SHARPE_GOOD      = 1.0    # Good risk-adjusted return
    SHARPE_MARGINAL  = 0.5    # Barely acceptable

    SORTINO_EXCELLENT = 2.0    # Strong downside-adjusted return
    SORTINO_GOOD      = 1.0    # Good downside-adjusted return
    SORTINO_MARGINAL  = 0.5    # Barely acceptable

    # ── COST EFFICIENCY THRESHOLDS ───────────────────────────
    COST_RATIO_EXCELLENT = 0.05    # Costs < 5% of gross profit
    COST_RATIO_GOOD      = 0.15    # Costs < 15% of gross profit
    COST_RATIO_MARGINAL  = 0.30    # Costs < 30% of gross profit

    # ── SAMPLE SIZE THRESHOLDS ───────────────────────────────
    TRADES_EXCELLENT = 100    # Statistically reliable
    TRADES_GOOD      = 50     # Reasonably reliable
    TRADES_MARGINAL  = 20     # Minimum meaningful sample

    def __init__(self, results: dict):
        """
        Initialise scorer with the full backtest results dict.
        Resolves all metrics safely from multiple possible sources.
        """

        # Store the full results dict for field access throughout
        self.results = results or {}

        # Extract the closed trades list — primary data source
        self.trades = self.results.get("trades", []) or []

        # Total trade count — derived from actual trade list
        self.total_trades = len(self.trades)

        # ── RESOLVE ALL METRICS ───────────────────────────────
        # Each resolver checks the trade list first,
        # then falls back to the reported summary value.
        # This prevents dashboard rounding from skewing the score.

        self.avg_r = self._resolve_avg_r()         # Average R-Multiple
        self.expectancy = self._resolve_expectancy_r()  # Expectancy in R (not dollars)
        self.win_rate = self._resolve_win_rate()      # Win rate percentage

        self.gross_profit, self.gross_loss = self._resolve_gross_pnl()
        self.profit_factor  = self._resolve_profit_factor()

        self.max_drawdown = self._as_float(self.results.get("max_drawdown", 100.0), 100.0)
        self.loss_streak = self._resolve_loss_streak()

        self.sharpe = self._as_float(self.results.get("sharpe_ratio", 0.0), 0.0)

        # Cap Sortino — values like 272 or 999 are meaningless artefacts
        # of tiny sample sizes and distort the edge quality score
        raw_sortino = self._as_float(self.results.get("sortino_ratio", 0.0), 0.0)
        self.sortino = min(raw_sortino, 10.0)    # Cap at 10 — anything above is noise

        self.total_fees = self._as_float(self.results.get("total_fees_paid", 0.0), 0.0)
        self.total_slippage = self._as_float(self.results.get("total_slippage_cost", 0.0), 0.0)
        self.final_balance  = self._as_float(self.results.get("final_balance", 0.0), 0.0)
        self.total_costs = self.total_fees + self.total_slippage
        self.cost_ratio = self._resolve_cost_ratio()

        # Regime and structure breakdowns — new in this version
        self.regime_breakdown = self.results.get("regime_breakdown", {}) or {}
        self.structure_breakdown = self.results.get("structure_breakdown", {}) or {}

        # Build all contextual note blocks
        self.regime_notes = self._build_regime_notes()
        self.structure_notes = self._build_structure_notes()    # NEW
        self.walk_forward = self._build_walk_forward_notes() # NEW
        self.true_walk_forward = self._build_true_walk_forward() # NEW
        self.robustness = self._build_robustness_test()         # NEW

        # Reliability factor based on sample size
        self.reliability_factor = self._get_reliability_factor()

        # Breakdown filled by evaluate()
        self.breakdown = {}

    # ============================================================
    # SAFE TYPE CONVERSION
    # ============================================================
    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        """Safely converts any value to float. Returns default on failure."""

        try:
            if value in (None, "", "None"):    # Treat empty values as missing
                return default
            return float(value)    # Convert to float
        except (TypeError, ValueError):
            return default    # Return default on any conversion error

    # ============================================================
    # METRIC RESOLVERS
    # Check trade list first — fall back to reported summary
    # ============================================================

    def _extract_trade_profit(self, trade: dict) -> float:
        """Extracts profit from a trade dict — checks multiple key names."""

        for key in ("profit", "pnl", "net_pnl"):    # Check all common profit keys
            if trade.get(key) is not None:           # Use first valid key found
                return self._as_float(trade.get(key), 0.0)
        return 0.0    # Default to zero if no profit key found

    def _extract_trade_r(self, trade: dict) -> float:
        """Extracts R-Multiple from a trade dict."""

        return self._as_float(trade.get("r_multiple", 0.0), 0.0)

    def _resolve_expectancy_r(self) -> float:
        """
        Calculates expectancy in R-Multiple units — NOT dollars.
        R-based expectancy is asset-agnostic: same scale for BTC and NEAR.
        A result of +0.3R means the average trade makes 0.3x the risk amount.
        """

        if self.trades:
            # Build list of R values from actual trade records
            r_values = [self._extract_trade_r(t) for t in self.trades]
            # Average R across all trades
            return round(sum(r_values) / len(r_values), 4) if r_values else 0.0

        # Fallback: use reported avg_r_multiple if no trade list
        return self._as_float(self.results.get("avg_r_multiple", 0.0), 0.0)

    def _resolve_avg_r(self) -> float:
        """Resolves average R-Multiple from trade list or reported value."""

        if self.trades:
            r_values = [
                self._extract_trade_r(t)
                for t in self.trades
                if t.get("r_multiple") is not None
            ]
            return round(sum(r_values) / len(r_values), 4) if r_values else 0.0

        return self._as_float(self.results.get("avg_r_multiple", 0.0), 0.0)

    def _resolve_win_rate(self) -> float:
        """Resolves win rate percentage from trade list or reported value."""

        if self.trades:
            profits = [self._extract_trade_profit(t) for t in self.trades]
            wins = sum(1 for p in profits if p > 0)
            return round((wins / len(profits)) * 100, 2) if profits else 0.0

        return self._as_float(self.results.get("win_rate", 0.0), 0.0)

    def _resolve_gross_pnl(self) -> tuple:
        """Resolves gross profit and gross loss totals from trade list."""

        if self.trades:
            profits = [self._extract_trade_profit(t) for t in self.trades]
            gross_profit = sum(p for p in profits if p > 0)
            gross_loss   = abs(sum(p for p in profits if p < 0))
            return gross_profit, gross_loss

        return 0.0, 0.0    # Fallback if no trade list

    def _resolve_profit_factor(self) -> float:
        """Resolves profit factor from trade list or reported value."""

        if self.trades:
            if self.gross_loss > 0:
                return round(self.gross_profit / self.gross_loss, 2)
            if self.gross_profit > 0:
                return 999.0    # No losses — perfect but not meaningful
            return 0.0

        return self._as_float(self.results.get("profit_factor", 0.0), 0.0)

    def _resolve_loss_streak(self) -> int:
        """Resolves max consecutive loss streak from trade list."""

        if not self.trades:
            return int(self.results.get("max_consecutive_losses", 999) or 999)

        current = 0    # Current streak counter
        worst   = 0    # Worst streak seen

        for trade in self.trades:
            profit = self._extract_trade_profit(trade)
            if profit < 0:
                current += 1                    # Extend losing streak
                worst = max(worst, current)     # Track worst
            else:
                current = 0                     # Reset on any win

        reported = int(self.results.get("max_consecutive_losses", 0) or 0)
        return max(worst, reported)    # Use larger of derived or reported

    def _resolve_cost_ratio(self) -> float:
        """Calculates total costs as a fraction of gross profit."""

        if self.gross_profit > 0:
            return self.total_costs / self.gross_profit
        return 1.0    # Max friction if no gross profit

    def _get_reliability_factor(self) -> float:
        """
        Returns a sample-size trust multiplier.
        Small samples cannot be trusted — this prevents a 10-trade
        backtest from achieving the same score as a 200-trade one.
        """

        if self.total_trades >= self.TRADES_EXCELLENT:    # 100+ trades
            return 1.0     # Full trust
        if self.total_trades >= self.TRADES_GOOD:         # 50+ trades
            return 0.95    # Slight discount
        if self.total_trades >= self.TRADES_MARGINAL:     # 20+ trades
            return 0.85    # Clear discount
        if self.total_trades >= 10:                       # 10-19 trades
            return 0.75    # Strong discount
        return 0.60        # Under 10 trades — heavy discount

    # ============================================================
    # REGIME NOTES — enhanced from previous version
    # ============================================================
    def _build_regime_notes(self) -> dict:
        """
        Summarises regime performance from the regime_breakdown dict.
        Enhanced to flag the specific worst regime to block in router.
        """

        if not self.regime_breakdown:
            return {
                "available": False,
                "message":   "No regime breakdown in results.",
            }

        rows = []    # Normalised regime rows

        for regime, data in self.regime_breakdown.items():
            trades   = int(data.get("trades", 0) or 0)
            profit   = self._as_float(data.get("total_profit", data.get("profit", 0.0)), 0.0)
            win_rate = self._as_float(data.get("win_rate", 0.0), 0.0)
            rows.append({
                "regime":   regime,
                "trades":   trades,
                "profit":   profit,
                "win_rate": win_rate,
            })

        if not rows:
            return {"available": False, "message": "Regime breakdown is empty."}

        # Find dominant, best, and worst regimes
        dominant      = max(rows, key=lambda r: r["trades"])
        best          = max(rows, key=lambda r: r["profit"])
        worst         = min(rows, key=lambda r: r["profit"])
        total_trades  = sum(r["trades"] for r in rows)
        dominance_pct = round(
            (dominant["trades"] / total_trades) * 100, 1
        ) if total_trades else 0.0

        # Build specific router suggestion for worst regime
        worst_regime   = worst["regime"]
        worst_profit   = round(worst["profit"], 2)
        worst_trades   = worst["trades"]
        router_suggestion = ""

        # claude code changed: gate actionable router suggestions on TRADES_MARGINAL —
        # was declaring a regime "worst" and recommending router config changes purely
        # from raw total profit, with no check on how many trades that regime actually
        # had. A regime with 2 unlucky trades looked identical to one with 200 — the
        # class already has a documented sample-size-reliability philosophy (see
        # TRADES_MARGINAL) and applies it to the numeric score, but not to this text.
        if worst_profit < 0 and worst_trades < self.TRADES_MARGINAL:
            router_suggestion = (
                f"{worst_regime} shows a loss (${worst_profit}) but only "
                f"{worst_trades} trade(s) so far — too few to act on. "
                f"Needs {self.TRADES_MARGINAL}+ trades in this regime before "
                f"changing router settings because of it."
            )
        elif worst_profit < 0:
            # Suggest how to block this regime in StrategyRouter settings
            if worst_regime in ("TRENDING_UP", "TRENDING_DOWN"):
                router_suggestion = (
                    f"Consider setting min_adx_for_trend higher to block "
                    f"weak {worst_regime} entries."
                )
            elif worst_regime == "RANGING":
                router_suggestion = (
                    f"Mean reversion is losing in RANGING. "
                    f"Tighten RSI thresholds in MeanReversionStrategy."
                )
            elif worst_regime == "HIGH_VOLATILITY":
                router_suggestion = (
                    f"HIGH_VOLATILITY trades are losing. "
                    f"Reduce atr_extreme_multiplier in RegimeDetector to block more."
                )

        return {
            "available":          True,
            "dominant_regime":    dominant["regime"],
            "dominance_pct":      dominance_pct,
            "best_regime":        best["regime"],
            "best_regime_profit": round(best["profit"], 2),
            "worst_regime":       worst["regime"],
            "worst_regime_profit": round(worst["profit"], 2),
            "worst_regime_trades": worst_trades,    # claude code changed: new — needed by _get_validation_penalty's sample-size gate
            "router_suggestion":  router_suggestion,    # NEW — specific fix suggestion
            "message": (
                f"Best: {best['regime']} (${round(best['profit'], 2)}) | "
                f"Worst: {worst['regime']} (${round(worst['profit'], 2)}) | "
                f"Dominant: {dominant['regime']} ({dominance_pct}%)"
            ),
        }

    # ============================================================
    # STRUCTURE NOTES — NEW
    # Analyses the structure_breakdown from backtester
    # ============================================================
    def _build_structure_notes(self) -> dict:
        """
        Analyses which price structure type (UPTREND/DOWNTREND/RANGING)
        produced the best and worst trade outcomes.

        This is the new analytics layer enabled by structure_analyser.py.
        It answers: "Are HH+HL entries actually better than LH+LL entries?"
        """

        if not self.structure_breakdown:
            return {
                "available": False,
                "message":   "No structure breakdown in results. "
                             "Ensure backtester.py is updated to extract structure fields.",
            }

        # Normalise structure names so TRENDING_UP and UPTREND mean the same thing.
        structure_map = {
            "TRENDING_UP": "UPTREND",
            "TRENDING_DOWN": "DOWNTREND",
            "UPTREND": "UPTREND",
            "DOWNTREND": "DOWNTREND",
            "RANGING": "RANGING",
            "CONTRACTING": "CONTRACTING",
            "EXPANDING": "EXPANDING",
            "UNCLEAR": "UNKNOWN",
            "UNKNOWN": "UNKNOWN",
        }

        rows = []    # Normalised structure rows

        for raw_structure, data in self.structure_breakdown.items():
            structure = structure_map.get(str(raw_structure).upper(), str(raw_structure).upper())
            trades   = int(data.get("trades", 0) or 0)
            profit   = self._as_float(data.get("total_profit", data.get("profit", 0.0)), 0.0)
            win_rate = self._as_float(data.get("win_rate", 0.0), 0.0)

            if trades == 0:
                continue    # Skip structures with no trades

            rows.append({
                "structure": structure,
                "trades":    trades,
                "profit":    profit,
                "win_rate":  win_rate,
            })

        if not rows:
            return {
                "available": False,
                "message":   "Structure breakdown has no trade data.",
            }

        # Prefer actionable structures over UNKNOWN when deciding best/worst.
        actionable_rows = [r for r in rows if r["structure"] != "UNKNOWN"] or rows

        # Find best and worst performing structure types
        best  = max(actionable_rows, key=lambda r: r["profit"])
        worst = min(actionable_rows, key=lambda r: r["profit"])

        # Check if uptrend and downtrend have opposite performance
        uptrend_row   = next((r for r in rows if r["structure"] == "UPTREND"),   None)
        downtrend_row = next((r for r in rows if r["structure"] == "DOWNTREND"), None)
        ranging_row   = next((r for r in rows if r["structure"] == "RANGING"),    None)

        # Determine directional bias — are longs or shorts performing better?
        directional_note = ""

        # claude code changed: gate the "disable shorts/longs" style recommendations on
        # both sides having TRADES_MARGINAL+ trades — same reasoning as the regime-notes
        # fix above. A 2-trade DOWNTREND sample being "losing" is noise, not a directional
        # edge finding, and shouldn't produce a confident router-config recommendation.
        both_sides_sufficient = (
            bool(uptrend_row) and bool(downtrend_row)
            and uptrend_row["trades"] >= self.TRADES_MARGINAL
            and downtrend_row["trades"] >= self.TRADES_MARGINAL
        )

        if uptrend_row and downtrend_row and not both_sides_sufficient:
            directional_note = (
                f"UPTREND has {uptrend_row['trades']} trade(s), DOWNTREND has "
                f"{downtrend_row['trades']} — at least one side has too few trades "
                f"(need {self.TRADES_MARGINAL}+ each) to draw a directional "
                f"conclusion yet."
            )
        elif uptrend_row and downtrend_row:
            if uptrend_row["profit"] > 0 and downtrend_row["profit"] < 0:
                directional_note = (
                    "LONG (HH+HL) trades are profitable. "
                    "SHORT (LH+LL) trades are losing. "
                    "Consider disabling shorts in router settings."
                )
            elif downtrend_row["profit"] > 0 and uptrend_row["profit"] < 0:
                directional_note = (
                    "SHORT (LH+LL) trades are profitable. "
                    "LONG (HH+HL) trades are losing. "
                    "Consider disabling longs in router settings."
                )
            elif uptrend_row["profit"] > 0 and downtrend_row["profit"] > 0:
                directional_note = (
                    "Both UPTREND and DOWNTREND structures are profitable. "
                    "Structure filter is working correctly."
                )
            else:
                directional_note = (
                    "Both UPTREND and DOWNTREND structures are losing. "
                    "The entry conditions need fundamental review."
                )
        elif ranging_row and ranging_row["profit"] > 0:
            directional_note = (
                "RANGING structure is producing the clearest edge. "
                "Trend structures are not dominating yet."
            )
        elif best["structure"] == "UNKNOWN":
            directional_note = (
                "Structure data is still too incomplete. "
                "Check that backtester.py is passing structure labels into the trade dict."
            )

        return {
            "available":           True,
            "best_structure":      best["structure"],
            "best_profit":         round(best["profit"], 2),
            "best_win_rate":       best["win_rate"],
            "worst_structure":     worst["structure"],
            "worst_profit":        round(worst["profit"], 2),
            "worst_win_rate":      worst["win_rate"],
            "directional_note":    directional_note,    # Long vs short bias
            "structure_rows":      rows,                # Full breakdown for display
            "message": (
                f"Best structure: {best['structure']} (${round(best['profit'], 2)}) | "
                f"Worst: {worst['structure']} (${round(worst['profit'], 2)}) | "
                f"{directional_note}"
            ),
        }

    # ============================================================
    # WALK-FORWARD NOTES — NEW
    # Splits trades into halves and compares performance
    # ============================================================
    def _build_walk_forward_notes(self) -> dict:
        """
        Splits closed trades chronologically into first and second half.
        Compares performance between halves to detect overfitting.

        If the second half is significantly worse than the first half,
        the strategy may be curve-fitted to early data and
        degrading as market conditions evolve.

        This is a simplified walk-forward test. A full walk-forward
        would use rolling windows — this gives a directional signal.
        """

        if len(self.trades) < 10:
            return {
                "available": False,
                "message":   f"Only {len(self.trades)} trades — need at least 10 for walk-forward split.",
            }

        # Split trades at the midpoint — first half vs second half
        midpoint   = len(self.trades) // 2    # Integer division for clean split
        first_half = self.trades[:midpoint]    # Earlier trades
        second_half = self.trades[midpoint:]   # Later trades

        # Calculate metrics for each half
        def half_stats(trade_list: list) -> dict:
            """Calculates key metrics for a subset of trades."""

            profits  = [self._extract_trade_profit(t) for t in trade_list]
            r_values = [self._extract_trade_r(t) for t in trade_list]

            wins     = sum(1 for p in profits if p > 0)
            total    = len(profits)
            win_rate = round((wins / total) * 100, 2) if total else 0.0
            avg_r    = round(sum(r_values) / len(r_values), 4) if r_values else 0.0
            net_pnl  = round(sum(profits), 2)

            return {
                "trades":   total,
                "wins":     wins,
                "win_rate": win_rate,
                "avg_r":    avg_r,
                "net_pnl":  net_pnl,
            }

        first_stats  = half_stats(first_half)     # First half performance
        second_stats = half_stats(second_half)    # Second half performance

        # Determine if strategy is degrading
        # Degrading means second half is significantly worse than first half
        win_rate_drop = first_stats["win_rate"] - second_stats["win_rate"]
        avg_r_drop    = first_stats["avg_r"]    - second_stats["avg_r"]
        pnl_drop      = first_stats["net_pnl"]  - second_stats["net_pnl"]

        # Classification thresholds
        # More than 15% win rate drop = significant degradation
        # More than 0.3R drop in avg R = significant degradation
        is_degrading = win_rate_drop > 15.0 or avg_r_drop > 0.3

        # More than 10% win rate drop = mild degradation — worth monitoring
        is_weakening = win_rate_drop > 10.0 or avg_r_drop > 0.15

        # Build the verdict
        if second_stats["net_pnl"] > 0 and first_stats["net_pnl"] > 0:
            if is_degrading:
                verdict = (
                    "DEGRADING — Strategy performed significantly better "
                    "in the first half than the second half. "
                    "Possible overfitting to early market conditions."
                )
                health = "DEGRADING"
            elif is_weakening:
                verdict = (
                    "WEAKENING — Strategy shows mild performance decline "
                    "in the second half. Monitor closely."
                )
                health = "WEAKENING"
            else:
                verdict = (
                    "STABLE — Strategy performs consistently across "
                    "both halves of the data. Good sign."
                )
                health = "STABLE"

        elif second_stats["net_pnl"] < 0 and first_stats["net_pnl"] > 0:
            verdict = (
                "FAILING — Strategy was profitable in the first half "
                "but lost money in the second half. "
                "Do not deploy — edge has broken down."
            )
            health = "FAILING"

        elif second_stats["net_pnl"] > 0 and first_stats["net_pnl"] < 0:
            verdict = (
                "IMPROVING — Strategy lost in the first half but "
                "became profitable in the second half. "
                "This may indicate the strategy needs a warm-up period."
            )
            health = "IMPROVING"

        else:
            verdict = (
                "BOTH HALVES LOSING — Strategy has no edge in either "
                "period. Fundamental entry logic needs review."
            )
            health = "NO_EDGE"

        return {
            "available":     True,
            "health":        health,           # STABLE/WEAKENING/DEGRADING/FAILING/IMPROVING
            "verdict":       verdict,          # Human readable conclusion
            "first_half":    first_stats,      # First half metrics
            "second_half":   second_stats,     # Second half metrics
            "win_rate_drop": round(win_rate_drop, 2),    # Drop in win rate
            "avg_r_drop":    round(avg_r_drop, 4),       # Drop in avg R
            "pnl_drop":      round(pnl_drop, 2),         # Drop in net P&L
            "split_at":      midpoint,                   # Where the split occurred
        }

    # ============================================================
    # TRUE WALK-FORWARD ENGINE — NEW (ROLLING WINDOWS)
    # ============================================================
    def _build_true_walk_forward(self) -> dict:
        """
        Performs rolling walk-forward validation.

        Instead of splitting trades into two halves,
        this method simulates a real-world process:

        Train on a window → Test on next window → Slide forward

        This is much closer to how strategies behave live.
        """

        if len(self.trades) < 30:
            return {
                "available": False,
                "message": "Not enough trades for rolling walk-forward (need 30+)."
            }

        window_size = max(10, len(self.trades) // 5)   # Each segment
        step_size   = max(5, window_size // 2)         # Overlapping windows

        segments = []
        index = 0

        while index + window_size * 2 <= len(self.trades):
            train = self.trades[index:index + window_size]
            test  = self.trades[index + window_size:index + window_size * 2]

            def calc_stats(trades):
                profits = [self._extract_trade_profit(t) for t in trades]
                r_vals  = [self._extract_trade_r(t) for t in trades]

                return {
                    "net": sum(profits),
                    "avg_r": sum(r_vals) / len(r_vals) if r_vals else 0.0,
                    "win_rate": (sum(1 for p in profits if p > 0) / len(profits) * 100) if profits else 0.0,
                }

            train_stats = calc_stats(train)
            test_stats  = calc_stats(test)

            degradation = train_stats["avg_r"] - test_stats["avg_r"]

            segments.append({
                "train_avg_r": round(train_stats["avg_r"], 4),
                "test_avg_r":  round(test_stats["avg_r"], 4),
                "train_win_rate": round(train_stats["win_rate"], 2),
                "test_win_rate": round(test_stats["win_rate"], 2),
                "degradation": round(degradation, 4),
            })

            index += step_size

        if not segments:
            return {"available": False, "message": "Unable to build WF segments."}

        avg_degradation = sum(s["degradation"] for s in segments) / len(segments)

        health = "STABLE"
        if avg_degradation > 0.3:
            health = "DEGRADING"
        elif avg_degradation > 0.15:
            health = "WEAKENING"

        return {
            "available": True,
            "segments": segments,
            "avg_degradation": round(avg_degradation, 4),
            "health": health,
        }

    # ============================================================
    # ROBUSTNESS TESTER — NEW
    # ============================================================
    def _build_robustness_test(self) -> dict:
        """
        Tests stability by shuffling trade order.

        If strategy collapses when order changes,
        it means edge is fragile (sequence-dependent).
        """

        if len(self.trades) < 20:
            return {
                "available": False,
                "message": "Not enough trades for robustness test."
            }

        baseline = self.expectancy

        simulations = []

        # claude code changed: seeded local Random instance instead of the
        # global random module — identical trade lists previously produced
        # different "stability" verdicts (ROBUST/SENSITIVE/FRAGILE) on
        # repeated runs since shuffle order wasn't reproducible. A local
        # instance (rather than random.seed() on the global module) avoids
        # side effects on any other code in the process that also uses
        # the random module.
        rng = random.Random(42)

        for _ in range(10):  # 10 perturbations
            shuffled = self.trades[:]
            rng.shuffle(shuffled)

            r_vals = [self._extract_trade_r(t) for t in shuffled]
            avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0

            simulations.append(avg_r)

        avg_sim = sum(simulations) / len(simulations)
        deviation = abs(baseline - avg_sim)

        stability = "ROBUST"
        if deviation > 0.3:
            stability = "FRAGILE"
        elif deviation > 0.15:
            stability = "SENSITIVE"

        return {
            "available": True,
            "baseline": round(baseline, 4),
            "avg_simulated": round(avg_sim, 4),
            "deviation": round(deviation, 4),
            "stability": stability,
        }

    # ============================================================
    # VALIDATION PENALTY — NEW
    # ============================================================
    def _get_validation_penalty(self) -> Dict[str, Any]:
        """Converts walk-forward and robustness findings into a small score penalty."""

        penalty = 0
        reasons = []

        # Walk-forward penalty
        if self.true_walk_forward.get("available"):
            wf_health = self.true_walk_forward.get("health", "STABLE")
            if wf_health == "DEGRADING":
                penalty += 5
                reasons.append("True walk-forward is degrading.")
            elif wf_health == "WEAKENING":
                penalty += 2
                reasons.append("True walk-forward is weakening.")

        # Robustness penalty
        if self.robustness.get("available"):
            stability = self.robustness.get("stability", "ROBUST")
            if stability == "FRAGILE":
                penalty += 5
                reasons.append("Robustness test is fragile.")
            elif stability == "SENSITIVE":
                penalty += 2
                reasons.append("Robustness test is sensitive.")

        # Structure penalty
        if self.structure_notes.get("available"):
            directional_note = self.structure_notes.get("directional_note", "").lower()
            if "both" in directional_note and "losing" in directional_note:
                penalty += 2
                reasons.append("Structure filter is not producing a directional edge.")

        # Regime penalty
        # claude code changed: gated on TRADES_MARGINAL — was penalizing the score for
        # "at least one regime is clearly losing" even when that regime had only a
        # handful of trades, the same sample-size blindness fixed in _build_regime_notes.
        if self.regime_notes.get("available"):
            worst_regime_profit = self.regime_notes.get("worst_regime_profit", 0.0)
            worst_regime_trades = self.regime_notes.get("worst_regime_trades", 0)
            if worst_regime_profit < 0 and worst_regime_trades >= self.TRADES_MARGINAL:
                penalty += 1
                reasons.append("At least one regime is clearly losing.")

        return {
            "penalty": penalty,
            "reasons": reasons,
        }

    # ============================================================
    # STRATEGY HEALTH — NEW
    # ============================================================
    def _resolve_strategy_health(self, final_total: float) -> str:
        """Aligns health with risk so score and health do not contradict each other."""

        # Hard risk override first.
        if self.max_drawdown > self.DRAWDOWN_MARGINAL:
            return "HIGH RISK"
        if self.loss_streak > self.LOSS_STREAK_MARGINAL:
            return "HIGH RISK"

        if self.true_walk_forward.get("available") and self.true_walk_forward.get("health") == "DEGRADING":
            return "HIGH RISK"
        if self.robustness.get("available") and self.robustness.get("stability") == "FRAGILE":
            return "HIGH RISK"

        if final_total >= 80 and self.max_drawdown <= self.DRAWDOWN_GOOD and self.loss_streak <= self.LOSS_STREAK_GOOD:
            return "HEALTHY"

        if final_total >= 60:
            return "MODERATE"

        return "WEAK"

    # ============================================================
    # EVALUATE — MAIN SCORING METHOD
    # ============================================================
    def evaluate(self) -> dict:
        """
        Runs all six scoring dimensions and returns the full report.
        The final score is discounted by the reliability factor
        based on sample size, then adjusted by validation penalty.
        """

        # Score each dimension
        prof = self._score_profitability()    # Profitability score
        cons = self._score_consistency()      # Consistency score
        risk = self._score_risk_control()     # Risk control score
        edge = self._score_edge_quality()     # Edge quality score
        cost = self._score_cost_efficiency()  # Cost efficiency score
        samp = self._score_sample_size()      # Sample size score

        # Sum raw scores before reliability discount
        raw_total = (
            prof["score"] +
            cons["score"] +
            risk["score"] +
            edge["score"] +
            cost["score"] +
            samp["score"]
        )

        # Apply reliability discount based on sample size
        # A 10-trade backtest cannot score as high as a 200-trade one
        reliability_adjusted = round(raw_total * self.reliability_factor, 1)

        # Apply validation penalty from walk-forward, robustness, structure, regime
        validation = self._get_validation_penalty()   # NEW
        validation_penalty = validation["penalty"]    # NEW

        # Final score after all discounts
        final_total = max(0.0, min(round(reliability_adjusted - validation_penalty, 1), 100.0))

        # Store per-dimension breakdown for dashboard display
        self.breakdown = {
            "profitability":   prof,
            "consistency":     cons,
            "risk_control":    risk,
            "edge_quality":    edge,
            "cost_efficiency": cost,
            "sample_size":     samp,
        }

        # Resolve strategy health so it matches risk reality
        strategy_health = self._resolve_strategy_health(final_total)

        # Get letter grade and plain-English verdict
        grade   = self._get_grade(final_total)
        verdict = self._get_verdict(final_total)

        # Override verdict if health is bad
        if strategy_health == "HIGH RISK":
            verdict = "HIGH RISK — Review before live deployment"
        elif strategy_health == "WEAK":
            verdict = "WEAK — Significant improvements required"

        # Build actionable suggestions based on weak dimensions
        suggestions = self._get_suggestions(final_total, raw_total)

        # Add validation-driven suggestions
        for reason in validation["reasons"]:
            suggestions.insert(0, reason)

        # Add health warning so the dashboard cannot miss it
        if strategy_health == "HIGH RISK":
            suggestions.insert(0, "Strategy health is HIGH RISK — fix risk issues before deployment.")

        return {
            # ── Core score ────────────────────────────────────
            "raw_score":           round(raw_total, 1),        # Before reliability discount
            "reliability_factor":  round(self.reliability_factor, 2),
            "reliability_score":   reliability_adjusted,       # After sample discount
            "validation_penalty":  validation_penalty,         # NEW
            "total_score":         final_total,                # Final dashboard score
            "grade":               grade,                      # Letter grade
            "verdict":             verdict,                    # Plain-English judgment
            "strategy_health":     strategy_health,            # NEW
            "health":              strategy_health,            # Alias for dashboard compatibility

            # ── Per-dimension breakdown ───────────────────────
            "breakdown":           self.breakdown,

            # ── Contextual analysis ───────────────────────────
            "regime_notes":        self.regime_notes,          # Regime performance analysis
            "structure_notes":     self.structure_notes,       # Structure analysis — NEW
            "walk_forward":        self.walk_forward,          # Simple half-split — NEW
            "true_walk_forward":   self.true_walk_forward,     # Rolling WF — NEW
            "robustness":          self.robustness,            # Robustness test — NEW
            "validation":          validation,                 # Penalty reasons — NEW

            # ── Actionable improvements ───────────────────────
            "suggestions":         suggestions,

            # ── Raw metrics for dashboard reference ───────────
            "metrics": {
                "expectancy_r":   self.expectancy,        # R-based expectancy — NEW
                "profit_factor":   self.profit_factor,
                "win_rate":       self.win_rate,
                "avg_r":          self.avg_r,
                "max_drawdown":   self.max_drawdown,
                "loss_streak":    self.loss_streak,
                "sharpe":         self.sharpe,
                "sortino":        self.sortino,            # Capped at 10
                "total_trades":   self.total_trades,
                "gross_profit":   round(self.gross_profit, 2),
                "gross_loss":     round(self.gross_loss, 2),
                "cost_ratio":     round(self.cost_ratio * 100, 1),
            },
        }

    # ============================================================
    # DIMENSION SCORERS
    # ============================================================

    def _score_profitability(self) -> dict:
        """Scores expectancy (R-based) and profit factor."""

        score = 0    # Start at zero
        notes = []   # Explanation notes

        # ── Expectancy — now R-based ──────────────────────────
        if self.expectancy >= self.EXPECTANCY_EXCELLENT:
            score += 15
            notes.append(f"Excellent expectancy +{self.expectancy}R per trade")
        elif self.expectancy >= self.EXPECTANCY_GOOD:
            score += 10
            notes.append(f"Good expectancy +{self.expectancy}R per trade")
        elif self.expectancy >= self.EXPECTANCY_MARGINAL:
            score += 5
            notes.append(f"Marginal expectancy {self.expectancy}R per trade")
        else:
            notes.append(f"Negative expectancy {self.expectancy}R per trade ❌")

        # ── Profit factor ─────────────────────────────────────
        if self.profit_factor >= self.PROFIT_FACTOR_EXCELLENT:
            score += 10
            notes.append(f"Excellent profit factor {self.profit_factor}")
        elif self.profit_factor >= self.PROFIT_FACTOR_GOOD:
            score += 7
            notes.append(f"Good profit factor {self.profit_factor}")
        elif self.profit_factor >= self.PROFIT_FACTOR_MARGINAL:
            score += 3
            notes.append(f"Marginal profit factor {self.profit_factor}")
        else:
            notes.append(f"Poor profit factor {self.profit_factor} ❌")

        return {"score": round(score, 1), "max": 25, "pct": round((score / 25) * 100, 1), "notes": notes}

    def _score_consistency(self) -> dict:
        """Scores win rate and average R-Multiple."""

        score = 0
        notes = []

        # ── Win rate ──────────────────────────────────────────
        if self.win_rate >= self.WIN_RATE_EXCELLENT:
            score += 12
            notes.append(f"Excellent win rate {self.win_rate}%")
        elif self.win_rate >= self.WIN_RATE_GOOD:
            score += 8
            notes.append(f"Good win rate {self.win_rate}%")
        elif self.win_rate >= self.WIN_RATE_MARGINAL:
            score += 4
            notes.append(f"Marginal win rate {self.win_rate}%")
        else:
            notes.append(f"Poor win rate {self.win_rate}% ❌")

        # ── Average R ─────────────────────────────────────────
        if self.avg_r >= self.AVG_R_EXCELLENT:
            score += 8
            notes.append(f"Excellent avg R +{self.avg_r}R")
        elif self.avg_r >= self.AVG_R_GOOD:
            score += 5
            notes.append(f"Good avg R +{self.avg_r}R")
        elif self.avg_r >= self.AVG_R_MARGINAL:
            score += 2
            notes.append(f"Marginal avg R {self.avg_r}R")
        else:
            notes.append(f"Negative avg R {self.avg_r}R ❌")

        return {"score": round(score, 1), "max": 20, "pct": round((score / 20) * 100, 1), "notes": notes}

    def _score_risk_control(self) -> dict:
        """Scores max drawdown and max consecutive loss streak."""

        score = 0
        notes = []

        # ── Max drawdown ──────────────────────────────────────
        if self.max_drawdown <= self.DRAWDOWN_EXCELLENT:
            score += 12
            notes.append(f"Excellent drawdown {self.max_drawdown}%")
        elif self.max_drawdown <= self.DRAWDOWN_GOOD:
            score += 8
            notes.append(f"Good drawdown {self.max_drawdown}%")
        elif self.max_drawdown <= self.DRAWDOWN_MARGINAL:
            score += 4
            notes.append(f"Marginal drawdown {self.max_drawdown}%")
        else:
            notes.append(f"Dangerous drawdown {self.max_drawdown}% ❌")

        # ── Max loss streak ───────────────────────────────────
        if self.loss_streak <= self.LOSS_STREAK_EXCELLENT:
            score += 8
            notes.append(f"Excellent loss streak {self.loss_streak}")
        elif self.loss_streak <= self.LOSS_STREAK_GOOD:
            score += 5
            notes.append(f"Good loss streak {self.loss_streak}")
        elif self.loss_streak <= self.LOSS_STREAK_MARGINAL:
            score += 2
            notes.append(f"Marginal loss streak {self.loss_streak}")
        else:
            notes.append(f"Dangerous loss streak {self.loss_streak} ❌")

        return {"score": round(score, 1), "max": 20, "pct": round((score / 20) * 100, 1), "notes": notes}

    def _score_edge_quality(self) -> dict:
        """Scores Sharpe ratio and Sortino ratio (capped at 10)."""

        score = 0
        notes = []

        # ── Sharpe ratio ──────────────────────────────────────
        if self.sharpe >= self.SHARPE_EXCELLENT:
            score += 8
            notes.append(f"Excellent Sharpe {self.sharpe}")
        elif self.sharpe >= self.SHARPE_GOOD:
            score += 5
            notes.append(f"Good Sharpe {self.sharpe}")
        elif self.sharpe >= self.SHARPE_MARGINAL:
            score += 2
            notes.append(f"Marginal Sharpe {self.sharpe}")
        else:
            notes.append(f"Poor Sharpe {self.sharpe} ❌")

        # ── Sortino ratio — capped at 10 ─────────────────────
        if self.sortino >= self.SORTINO_EXCELLENT:
            score += 7
            notes.append(f"Excellent Sortino {self.sortino}")
        elif self.sortino >= self.SORTINO_GOOD:
            score += 4
            notes.append(f"Good Sortino {self.sortino}")
        elif self.sortino >= self.SORTINO_MARGINAL:
            score += 2
            notes.append(f"Marginal Sortino {self.sortino}")
        else:
            notes.append(f"Poor Sortino {self.sortino} ❌")

        return {"score": round(score, 1), "max": 15, "pct": round((score / 15) * 100, 1), "notes": notes}

    def _score_cost_efficiency(self) -> dict:
        """Scores total execution costs relative to gross profit."""

        score = 0
        notes = []

        cost_ratio = self.cost_ratio    # Pre-calculated in __init__

        if cost_ratio <= self.COST_RATIO_EXCELLENT:
            score += 10
            notes.append(f"Excellent cost efficiency ({round(cost_ratio * 100, 1)}% of profit)")
        elif cost_ratio <= self.COST_RATIO_GOOD:
            score += 6
            notes.append(f"Good cost efficiency ({round(cost_ratio * 100, 1)}% of profit)")
        elif cost_ratio <= self.COST_RATIO_MARGINAL:
            score += 3
            notes.append(f"Marginal cost efficiency ({round(cost_ratio * 100, 1)}% of profit)")
        else:
            notes.append(f"Poor cost efficiency — {round(cost_ratio * 100, 1)}% of profit lost to costs ❌")

        return {
            "score":      round(score, 1),
            "max":        10,
            "pct":        round((score / 10) * 100, 1),
            "notes":      notes,
            "cost_ratio": round(cost_ratio * 100, 1),
        }

    def _score_sample_size(self) -> dict:
        """Scores sample size and returns the reliability context note."""

        score = 0
        notes = []

        if self.total_trades >= self.TRADES_EXCELLENT:
            score += 10
            notes.append(f"Excellent sample size ({self.total_trades} trades)")
        elif self.total_trades >= self.TRADES_GOOD:
            score += 6
            notes.append(f"Good sample size ({self.total_trades} trades)")
        elif self.total_trades >= self.TRADES_MARGINAL:
            score += 3
            notes.append(f"Marginal sample ({self.total_trades} trades) — increase data window")
        else:
            notes.append(
                f"Insufficient sample ({self.total_trades} trades) — "
                f"results unreliable. Score discounted by {round((1 - self.reliability_factor) * 100)}% ❌"
            )

        return {"score": round(score, 1), "max": 10, "pct": round((score / 10) * 100, 1), "notes": notes}

    # ============================================================
    # GRADE AND VERDICT
    # ============================================================

    def _get_grade(self, score: float) -> str:
        """Converts adjusted score to a letter grade."""

        if score >= 90: return "A+"
        if score >= 80: return "A"
        if score >= 70: return "B+"
        if score >= 60: return "B"
        if score >= 50: return "C"
        if score >= 40: return "D"
        return "F"

    def _get_verdict(self, score: float) -> str:
        """Converts adjusted score to a plain-English verdict."""

        if score >= 85: return "INSTITUTIONAL GRADE — Ready for real capital deployment"
        if score >= 70: return "STRONG STRATEGY — Consider small live deployment"
        if score >= 55: return "PROMISING — Needs improvement before live trading"
        if score >= 40: return "ALMOST THERE — Monitor once deployed live required"
        return "POOR — Do not trade with real capital"

    # ============================================================
    # SUGGESTIONS ENGINE — enhanced with structure and walk-forward
    # ============================================================

    def _get_suggestions(self, adjusted_score: float, raw_score: float) -> List[str]:
        """
        Builds a prioritised list of actionable improvement suggestions.
        Enhanced to include structure and walk-forward insights.
        """

        suggestions: List[str] = []

        # ── Walk-forward health check — highest priority ──────
        if self.walk_forward.get("available"):
            health = self.walk_forward.get("health", "")
            if health == "FAILING":
                suggestions.append(
                    "CRITICAL: Strategy was profitable in first half but "
                    "lost in second half. The edge has broken down. "
                    "Do not deploy until root cause is identified."
                )
            elif health == "DEGRADING":
                suggestions.append(
                    f"Strategy is degrading — win rate dropped "
                    f"{self.walk_forward.get('win_rate_drop', 0):.1f}% "
                    f"in the second half. Likely overfitted to early data."
                )
            elif health == "WEAKENING":
                suggestions.append(
                    "Strategy shows mild performance decline in later trades. "
                    "Monitor closely if deployed."
                )

        # ── True walk-forward check — NEW ────────────────────
        if self.true_walk_forward.get("available"):
            wf_health = self.true_walk_forward.get("health", "STABLE")
            if wf_health == "DEGRADING":
                suggestions.append(
                    "True rolling walk-forward is degrading. "
                    "The edge weakens as the window moves forward."
                )
            elif wf_health == "WEAKENING":
                suggestions.append(
                    "True rolling walk-forward is weakening. "
                    "The strategy may be sensitive to market drift."
                )

        # ── Robustness check — NEW ────────────────────────────
        if self.robustness.get("available"):
            stability = self.robustness.get("stability", "ROBUST")
            if stability == "FRAGILE":
                suggestions.append(
                    "Robustness test is fragile. "
                    "The edge changes too much when trade order is perturbed."
                )
            elif stability == "SENSITIVE":
                suggestions.append(
                    "Robustness test is sensitive. "
                    "The edge may be real, but it is not yet sturdy."
                )

        # ── Structure insights ────────────────────────────────
        if self.structure_notes.get("available"):
            directional = self.structure_notes.get("directional_note", "")
            if directional:
                suggestions.append(directional)

        # ── Regime insights ───────────────────────────────────
        if self.regime_notes.get("available"):
            router_suggestion = self.regime_notes.get("router_suggestion", "")
            if router_suggestion:
                suggestions.append(router_suggestion)

        # ── Profitability ─────────────────────────────────────
        if self.expectancy < self.EXPECTANCY_GOOD:
            suggestions.append(
                f"Expectancy is {self.expectancy}R — below +0.2R target. "
                "Widen take profit or tighten stop to improve RRR."
            )

        if self.profit_factor < self.PROFIT_FACTOR_GOOD:
            suggestions.append(
                f"Profit factor {self.profit_factor} is below 1.5 target. "
                "Winners are not covering losers sufficiently."
            )

        # ── Consistency ───────────────────────────────────────
        if self.win_rate < self.WIN_RATE_MARGINAL:
            suggestions.append(
                f"Win rate {self.win_rate}% is critically low. "
                "Review entry conditions — structure filter may be too loose."
            )

        if self.avg_r < self.AVG_R_MARGINAL:
            suggestions.append(
                f"Avg R of {self.avg_r}R is negative. "
                "The average trade loses more than it gains — no edge present."
            )

        # ── Risk control ──────────────────────────────────────
        if self.max_drawdown > self.DRAWDOWN_GOOD:
            suggestions.append(
                f"Drawdown of {self.max_drawdown}% is too high for live trading. "
                "Reduce RISK_PER_TRADE or add a daily loss limit."
            )

        if self.loss_streak > self.LOSS_STREAK_GOOD:
            suggestions.append(
                f"Loss streak of {self.loss_streak} is dangerous. "
                "Add a circuit breaker that pauses trading after 4 consecutive losses."
            )

        # ── Sample size ───────────────────────────────────────
        if self.total_trades < self.TRADES_GOOD:
            suggestions.append(
                f"Only {self.total_trades} trades — increase data window to 2+ years "
                "to get 100+ trades for reliable statistics."
            )

        # ── Success message if nothing is wrong ───────────────
        if not suggestions:
            suggestions.append(
                "Strategy is performing well across all dimensions. "
                "Move to walk-forward testing on a separate data window."
            )

        return suggestions