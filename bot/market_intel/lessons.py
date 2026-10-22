# bot/market_intel/lessons.py
"""Beginner explanations adapted to the selected asset's measured numbers.   # claude code changed: 2026-10-21 — new

Educational content, deterministic (no AI). Numbers inserted come from quant.py; the arithmetic examples are worked
illustrations, labelled as such. Nothing here is personalised financial advice.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def _round_trip_cost_pct(asset_class: str, price: Optional[float], symbol: str) -> Optional[float]:
    if asset_class == "crypto":
        from bot.config.execution_costs import get_venue_execution_costs
        c = get_venue_execution_costs("binance")
        return 2 * (c["fee_rate"] + c["slippage_rate"]) * 100
    from bot.market_intel.quant import _cost_model
    if not price:
        return None
    sp = _cost_model(asset_class, symbol, price)["spread_price"]
    return sp / price * 100 if sp else None


def lessons(asset_class: str, symbol: str, overview: Dict, regime: Dict, evidence: Dict) -> List[Dict]:
    pair = symbol.replace("_", "/")
    price = overview.get("price")
    metrics = {m["key"]: m.get("value") for m in (evidence or {}).get("metrics", [])}
    vol = metrics.get("vol_now")
    rt = _round_trip_cost_pct(asset_class, price, symbol)
    rt_txt = f"{rt:.3f} %" if rt else "unknown for this pair"
    reg = (regime or {}).get("regime", "unavailable")
    out = [
        {"id": "overbought", "title": "Why can an 'overbought' market keep rising?",
         "body": "Indicators such as RSI measure how far price has moved recently, not whether it must turn. In a "
                 "persistent trend, buyers can keep arriving for weeks, so 'overbought' can stay overbought. "
                 f"Right now the classifier calls {pair} {reg} on the {overview.get('timeframe')} timeframe — in a "
                 "trending regime, 'overbought' is a weaker reversal hint than in a range.",
         "kind": "education"},
        {"id": "win_rate", "title": "Why a high win rate is not the same as a profitable strategy",
         "body": "Profit = (wins × average win) − (losses × average loss). Worked illustration: win 9 times out of 10 "
                 "at +$10, lose once at −$120 → +$90 − $120 = −$30. A 90 % win rate, and still a loss. DCA ladders on "
                 "this platform show exactly this shape: ~82 % of cycles win, but the rare full-ladder losses are large "
                 "(see the DCA Engine page).",
         "kind": "education"},
        {"id": "costs", "title": "How trading costs can erase a small edge",
         "body": f"A round trip in {pair} costs about {rt_txt} with this platform's cost model "
                 f"({'Binance taker fee + slippage, both sides' if asset_class == 'crypto' else 'median MT5 spread; commission unknown'}). "
                 "Worked illustration: a strategy that averages +0.20 % per trade before costs and trades 200 times a "
                 f"year earns +40 % gross; subtract {rt_txt} × 200 and the net can be far smaller or negative. Always "
                 "compare the average gain per trade with the cost per trade.",
         "kind": "education"},
        {"id": "mean_reversion_trend", "title": "Why mean-reversion fails during strong trends",
         "body": "Mean-reversion buys weakness expecting a bounce. In a strong down-trend the 'weakness' keeps coming, "
                 "so each purchase averages into a falling market — the DCA ladder's worst losses happened in such "
                 "moves (e.g. GBP/JPY in Aug 2024). Check the regime before assuming prices will return to an average.",
         "kind": "education"},
        {"id": "backtest_oos", "title": "Backtest versus out-of-sample validation",
         "body": "A backtest replays rules on history you have already seen — you may have (even unknowingly) chosen "
                 "rules that fit that history. Out-of-sample validation tests rules on data that played no part in "
                 "choosing them, ideally data that did not exist yet. On this platform, data after 2026-10-04 "
                 "(crypto) is sealed for exactly that purpose and will be opened once, on schedule.",
         "kind": "education"},
        {"id": "support", "title": "Why a support level is a hypothesis, not a guarantee",
         "body": "A 'support level' is a price where buying appeared before. Nothing forces buyers to appear again. "
                 f"The workspace shows the recent range low for {pair} as an observed extreme — treat it as a level to "
                 "watch, and decide in advance what you would do if it breaks.",
         "kind": "education"},
    ]
    if vol:
        out.append({"id": "volatility", "title": "Reading the volatility number",
                    "body": f"Annualised realised volatility for {pair} is about {vol:.1f} % right now. Rough rule: daily "
                            f"moves of about {vol / 19.1:.2f} % (vol ÷ √365) are ordinary; a move several times that size "
                            "is unusual. Position sizes should shrink when this number rises.",
                    "kind": "education (uses a live system-verified number)"})
    for o in out:
        o["disclaimer"] = "Educational explanation — not personalised financial advice and not a trading signal."
    return out
