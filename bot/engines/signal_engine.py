import pandas as pd

# claude code changed: was "from bot.strategies.moving_average import
# moving_average_strategy" — that function has never existed in this repo;
# bot/strategies/moving_average.py defines a class, MovingAverageStrategy,
# with a check_signal(df) method (confirmed via bot/engines/strategy_router.py,
# which calls self.trend_strategy.check_signal(df)). This import has been
# silently broken since whenever the strategy moved from a function to a
# class — caught by manage.py health_check's "Signal Engine" check, which
# has been reporting "generate_signal not importable" ever since. Nothing
# in the live bot_runner.py/backtester.py pipeline actually imports this
# module (confirmed via grep — StrategyRouter calls MovingAverageStrategy
# directly), so the break never affected real trading; fixed anyway so the
# health check reports a real pass instead of masking a genuinely broken
# module behind "warn, skipping check".
from bot.strategies.moving_average import MovingAverageStrategy

# optional import (create file if not existing)
from bot.engines.trade_filter import trade_quality_filter


# =========================
# INIT ONCE (performance)
# =========================

# claude code changed: new — instantiated once at module load, same pattern
# StrategyRouter uses, instead of constructing a fresh MovingAverageStrategy
# on every generate_signal() call.
_trend_strategy = MovingAverageStrategy()

def generate_signal(df, i):

    sub_df = df.iloc[:i]

    if len(sub_df) < 60:
        return {
            "signal": "NO_SIGNAL",
            "reason": "insufficient_data"
        }

    # claude code changed: was moving_average_strategy(sub_df) — see the
    # import comment above for why that never worked.
    signal = _trend_strategy.check_signal(sub_df)

    # ✅ HARD GUARD: strategy must NEVER return None
    if not signal or not isinstance(signal, dict):
        return {
            "signal": "NO_SIGNAL",
            "reason": "strategy_returned_none"
        }
    if signal.get("signal") in ["BUY", "SELL"]:
        is_quality, filter_reason = trade_quality_filter(signal)
        if not is_quality:
            return {
                "signal": "NO_SIGNAL",
                "reason": f"rejected_by_filter: {filter_reason}"
            }
        
    return signal