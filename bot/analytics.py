from collections import defaultdict


class TradeAnalytics:
    """
    Handles tracking and analyzing trading performance.
    """

    def __init__(self):
        self.rejections = defaultdict(int)
        self.trades = []

    # =========================
    # REJECTIONS
    # =========================
    def record_rejection(self, reason: str):
        if reason not in self.rejections:
            self.rejections[reason] += 1

    def rejection_summary(self):
        return dict(self.rejections)

    def rejection_table(self):
        total = sum(self.rejections.values())

        table = {}

        for key, count in self.rejections.items():

            percentage = (count / total * 100) if total > 0 else 0

            table[key] = {
                "count": count,
                "percentage": round(percentage, 2),
            }

        return table

    # =========================
    # TRADES
    # =========================
    def record_trade(self, trade: dict):
        self.trades.append(trade)

    def summary(self):
        total_trades = len(self.trades)

        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "total_pnl": 0
            }

        wins = [t for t in self.trades if t.get("profit", 0) > 0]
        losses = [t for t in self.trades if t.get("profit", 0) <= 0]

        total_pnl = sum(t.get("profit", 0) for t in self.trades)
        win_rate = (len(wins) / total_trades) * 100

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
        }

    # =========================
    # RSI BREAKDOWN
    # =========================
    def rsi_analysis(self):
        if not self.trades:
            return {}

        buckets = defaultdict(list)

        for t in self.trades:
            rsi = t.get("rsi")

            if rsi is None:
                continue

            bucket = int(rsi // 10) * 10
            buckets[bucket].append(t.get("profit", 0))

        result = {}

        for k, v in buckets.items():
            result[f"{k}-{k+9}"] = {
                "trades": len(v),
                "avg_profit": round(sum(v) / len(v), 4) if v else 0
            }

        return result