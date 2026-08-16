# ============================================================
# bot/config/risk.py
# claude code changed: new file — fixes architecture audit finding M3.
# risk_pct=0.01 (1% risk per trade) was independently defined in three
# places that happened to agree by coincidence, not by any shared
# source: PositionSizer.__init__'s own default, ExecutionEngine's
# explicit PositionSizer(risk_pct=0.01) call, and backtester.py's
# module-level RISK_PER_TRADE constant. Nothing kept them in sync — a
# change to one silently would not propagate to the others. This file
# is now the single source; see position_sizer.py, execution_engine.py,
# and backtester.py for the call sites that were updated to use it.
# ============================================================

# Fraction of account balance risked per trade under fixed-fractional
# sizing. 0.01 = 1%.
DEFAULT_RISK_PCT = 0.01

# Hard safety ceiling — PositionSizer refuses to size above this even
# if risk_pct is misconfigured higher (e.g. 10 passed instead of 0.10).
MAX_RISK_PCT = 0.05
