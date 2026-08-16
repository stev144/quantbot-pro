import pandas as pd

from bot.strategies.moving_average import moving_average_strategy
#from bot.strategies.mean_reversion_strategy import MeanReversionStrategy
#from bot.engines.regime_detector import RegimeDetector

# optional import (create file if not existing)
from bot.engines.trade_filter import trade_quality_filter


# =========================
# INIT ONCE (performance)
# =========================

#regime_detector = RegimeDetector()
#mr_strategy = MeanReversionStrategy()

def generate_signal(df, i):

    sub_df = df.iloc[:i]

    if len(sub_df) < 60:
        return {
            "signal": "NO_SIGNAL",
            "reason": "insufficient_data"
        }

    signal = moving_average_strategy(sub_df)

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