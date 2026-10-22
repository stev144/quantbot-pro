# bot/market_intel — Market Intelligence workspace services.   # claude code changed: 2026-10-21 — new package
#
# Boundaries (one module each):
#   quant.py      market data -> overview, regime, evidence metrics, risk scenario (Python is the source of truth)
#   news_ai.py    Anthropic Claude service: source-attributed news (web search) -> schema-validated structuring
#   exposure.py   authorised open exposure (TradeRecord) + deterministic alerts
#   evidence.py   research results for the asset, classified by bot.research.evidence_registry
#   lessons.py    beginner explanations adapted to the asset's measured numbers
#   history_lab.py "What happened next?" point-in-time lab over research data (sealed holdouts respected)
# Nothing here places orders, changes risk limits or writes to data/.
