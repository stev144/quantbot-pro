# bot/market_intel/exposure.py
"""Exposure-aware alerts for the Market Intelligence workspace.   # claude code changed: 2026-10-21 — new

Exposure comes ONLY from the bot's own journal (TradeRecord rows with status OPEN) and is shown only to staff
(operators) — TradeRecord is the platform's trading book, not a per-user portfolio. Alerts are informational or
"review" prompts; this module never reports a risk-limit breach (those are evaluated by the live risk manager,
not here) and never changes limits, positions or orders.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

INFO, REVIEW = "INFORMATIONAL", "REVIEW"


def _symbol_variants(symbol: str) -> List[str]:
    base, quote = symbol.split("_")
    return [f"{base}/{quote}", f"{base}{quote}", symbol]


def open_exposure(user, asset_class: str, symbol: str) -> Dict:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_staff", False):
        return {"available": False,
                "reason": "Exposure is shown to platform operators only. General market-risk context is still shown below."}
    from bot.journal.models import TradeRecord
    rows = TradeRecord.objects.filter(symbol__in=_symbol_variants(symbol), status="OPEN").order_by("-created_at")[:20]
    positions = [{"symbol": r.symbol, "venue": r.venue, "side": r.side, "quantity": r.quantity, "entry_price": r.entry_price,
                  "opened": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
                  "stop_loss": getattr(r, "sl", None), "take_profit": getattr(r, "tp", None)} for r in rows]
    total_open = TradeRecord.objects.filter(status="OPEN").count()
    return {"available": True, "positions": positions, "open_positions_all_symbols": total_open,
            "source": "bot.journal.models.TradeRecord (status=OPEN) — the bot's own trade journal",
            "note": "Paper / research simulations (DCA engine, QC paper trader) are not trading positions and are not included."}


def build_alerts(*, asset_class: str, symbol: str, exposure: Dict, evidence: Dict, regime: Dict,
                 news: Optional[Dict] = None, now: Optional[pd.Timestamp] = None) -> List[Dict]:
    now = now or pd.Timestamp.now(tz="UTC")
    alerts: List[Dict] = []
    metrics = {m["key"]: m for m in (evidence or {}).get("metrics", [])}
    has_pos = bool(exposure.get("available") and exposure.get("positions"))

    rank = (metrics.get("vol_rank") or {}).get("value")
    if rank is not None and rank >= 80:
        alerts.append({"level": REVIEW if has_pos else INFO, "instrument": symbol.replace("_", "/"),
                       "reason": f"Realised volatility is in the top {100 - rank:.0f} % of this sample (rank {rank:.0f}).",
                       "source": "SYSTEM-VERIFIED: quant.evidence vol_rank", "timestamp": now.isoformat(),
                       "confidence": "measured (descriptive)",
                       "action": "Review stop distances and position sizes that were set under calmer conditions." if has_pos
                       else "Position sizes based on normal volatility would carry more risk than intended."})
    if (regime or {}).get("regime") == "HIGH_VOLATILITY":
        alerts.append({"level": REVIEW if has_pos else INFO, "instrument": symbol.replace("_", "/"),
                       "reason": "The regime classifier reports HIGH_VOLATILITY on this timeframe.",
                       "source": "SYSTEM-VERIFIED: RegimeDetector", "timestamp": now.isoformat(),
                       "confidence": f"indicator agreement {regime.get('agreement')}",
                       "action": "The live strategy router does not open trades in this regime; review open risk."})
    if has_pos:
        alerts.append({"level": REVIEW, "instrument": symbol.replace("_", "/"),
                       "reason": f"{len(exposure['positions'])} open position(s) in this instrument in the bot's journal.",
                       "source": "SYSTEM-VERIFIED: TradeRecord", "timestamp": now.isoformat(), "confidence": "recorded fact",
                       "action": "Check these positions against the regime, volatility and news on this page."})
    for item in (news or {}).get("items", []) or []:
        if item.get("severity") == "high":
            alerts.append({"level": REVIEW if has_pos else INFO, "instrument": symbol.replace("_", "/"),
                           "reason": f"High-severity news (AI-assessed): {item.get('headline')}",
                           "source": f"AI INTERPRETATION of {item.get('publisher')} — {item.get('url')}",
                           "timestamp": now.isoformat(), "confidence": f"AI confidence {item.get('confidence')} (qualitative)",
                           "action": "Read the original article; treat the AI assessment as a prompt for review, not a signal."})
    for a in alerts:
        a["is_limit_breach"] = False   # breaches are decided by the live risk manager, never by this page
    return alerts
