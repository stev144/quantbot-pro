#bot/engines/analytics_engine.py

from collections import defaultdict

class TradeAnalytics:
    def __init__(self):
        self.trades = []

        # Rejection reasons (auto-extends if new ones appear)
        self.rejections = {
            "missing_data": 0,
            "not_enough_data": 0,
            "indicator_warmup": 0,
            "invalid_range": 0,
            "flat_market": 0,
            "no_trend": 0,
            "no_pullback": 0,
            "rsi_out_of_range": 0,
            "no_momentum": 0,
            "no_setup": 0,
            "no_valid_setup": 0,
            "other": 0,
        }

        # Optional richer diagnostics
        self.rejection_meta = defaultdict(list)

    # =========================
    # INTERNAL HELPERS
    # =========================
    @staticmethod
    def _normalize_reason(reason: str) -> str:
        reason = (reason or "other").lower().strip().replace(" ", "_")

        # normalize common variations safely
        if reason in ("no_signal", "no-signal"):
            return "no_setup"

        if reason in ("no_setup", "nosetup"):
            return "no_setup"

        return reason

    # =========================
    # TRADE RECORDING
    # =========================
    def record_trade(self, trade: dict):
        if trade is None:
            return

        pnl = trade.get("pnl")
        if pnl is None:
            return

        self.trades.append(trade)

    # =========================
    # REJECTION RECORDING
    # =========================
    def record_rejection(self, reason: str, meta: dict = None):
        reason = self._normalize_reason(reason)

        if reason not in self.rejections:
            self.rejections[reason] = 0

        self.rejections[reason] += 1

        if meta:
            self.rejection_meta[reason].append(meta)

    # =========================
    # SUMMARY
    # =========================
    def summary(self):
        total = len(self.trades)

        if total == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
            }

        wins = sum(1 for t in self.trades if float(t.get("pnl", 0)) > 0)
        losses = sum(1 for t in self.trades if float(t.get("pnl", 0)) <= 0)
        total_pnl = sum(t.get("pnl", 0) for t in self.trades)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / total) * 100, 2),
            "total_pnl": round(total_pnl, 2),
        }

    # =========================
    # STRUCTURE ANALYSIS
    # =========================
    def structure_breakdown(self):
        buckets = defaultdict(list)

        for t in self.trades:
            structure = t.get("structure", "UNKNOWN")
            pnl = t.get("pnl")

            if pnl is None:
                continue

            buckets[structure].append(pnl)

        table = {}
        best_structure = None
        worst_structure = None
        best_pnl = float("-inf")
        worst_pnl = float("inf")

        for structure, pnls in buckets.items():
            total = len(pnls)
            total_pnl = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)

            win_rate = (wins / total) * 100 if total else 0

            table[structure] = {
                "trades": total,
                "win_rate": round(win_rate, 2),
                "pnl": round(total_pnl, 2),
            }

            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_structure = structure

            if total_pnl < worst_pnl:
                worst_pnl = total_pnl
                worst_structure = structure

        return {
            "table": table,
            "best_structure": best_structure,
            "worst_structure": worst_structure,
        }

    # =========================
    # RSI ANALYSIS
    # =========================
    def rsi_breakdown(self):
        buckets = defaultdict(list)

        for t in self.trades:
            rsi = t.get("rsi")
            pnl = t.get("pnl")
            direction = t.get("direction")

            if rsi is None or pnl is None:
                continue

            try:
                rsi = int(rsi)
            except Exception:               
                continue

            bucket = (rsi // 10) * 10
            buckets[bucket].append({
                "pnl": pnl,
                "direction": direction
            })

        result = {}

        for k, trades in buckets.items():
            total = len(trades)

            wins = [t for t in trades if t["pnl"] > 0]
            long_trades = [t for t in trades if t["direction"] == "LONG"]
            short_trades = [t for t in trades if t["direction"] == "SHORT"]

            long_wins = [t for t in long_trades if t["pnl"] > 0]
            short_wins = [t for t in short_trades if t["pnl"] > 0]

            result[f"{k}-{k+9}"] = {
                "trades": total,
                "win_rate": round((len(wins) / total) * 100, 2) if total else 0,
                "avg_profit": round(sum(t["pnl"] for t in trades) / total, 2) if total else 0,

                "long_trades": len(long_trades),
                "long_win_rate": round((len(long_wins) / len(long_trades)) * 100, 2) if long_trades else 0,

                "short_trades": len(short_trades),
                "short_win_rate": round((len(short_wins) / len(short_trades)) * 100, 2) if short_trades else 0,
            }

        return result

    # =========================
    # REJECTION SUMMARY
    # =========================
    def rejection_summary(self):
        return self.rejections

    # =========================
    # REJECTION TABLE (FLAT + DASHBOARD SAFE)
    # =========================
    def rejection_table(self):
        total = sum(self.rejections.values())

        if total == 0:
            return {}

        table = {}

        for reason, count in self.rejections.items():
            key = str(reason).strip().lower().replace(" ", "_")

            table[key] = {
                "count": count,
                "percentage": round((count / total) * 100, 2),
            }

        return table

    # =========================
    # REJECTION INTELLIGENCE
    # =========================
    def rejection_insights(self):
        table = self.rejection_table()

        if not table:
            return {
                "summary": {
                    "total_rejections": 0,
                    "top_reason": None,
                    "top_count": 0,
                    "top_percentage": 0,
                },
                "insights": ["No rejection data available."],
            }

        sorted_items = sorted(
            table.items(),
            key=lambda x: x[1]["count"],
            reverse=True
        )

        total_rejections = sum(v["count"] for v in table.values())
        top_reason, top_data = sorted_items[0]

        insights = []

        if top_data["percentage"] >= 40:
            insights.append(
                f"Over-filtering detected: '{top_reason}' blocks {top_data['percentage']}% of setups."
            )

        for reason, data in sorted_items[:5]:
            pct = data["percentage"]

            if "no_extreme" in reason:
                insights.append(
                    f"'no_extreme' is blocking {pct}% of signals — entry thresholds may be too strict."
                )
            elif "momentum" in reason:
                insights.append(
                    f"Momentum filter is blocking {pct}% of setups — consider relaxing momentum thresholds."
                )
            elif "adx" in reason:
                insights.append(
                    f"ADX-based rejection is blocking {pct}% of setups — trend filter may be too strict."
                )
            elif "structure" in reason:
                insights.append(
                    f"Structure-related rejection is blocking {pct}% of setups — structure logic may need review."
                )

        if not insights:
            insights.append("Rejection pattern looks balanced. No dominant bottleneck detected.")

        return {
            "summary": {
                "total_rejections": total_rejections,
                "top_reason": top_reason,
                "top_count": top_data["count"],
                "top_percentage": top_data["percentage"],
            },
            "insights": insights,
        }