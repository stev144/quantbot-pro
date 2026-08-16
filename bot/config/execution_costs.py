# ============================================================
# bot/config/execution_costs.py
# claude code changed: new file — bot/config package did not exist,
# so bot/engines/market_data.py's "from bot.config.execution_costs
# import CANDLE_CACHE_TTL" raised ImportError at module load time,
# which crashed bot_runner.py before run_bot() could ever execute
# (see architecture audit, finding C1).
# ============================================================

# Seconds a fetched OHLCV candle set is considered fresh before
# MarketData.get_ohlcv() re-fetches from the exchange. Short by design:
# this cache exists to collapse duplicate calls made in quick succession
# within the same loop iteration, not to span the 3600s candle interval
# bot_runner.py itself sleeps for. Override per-instance via
# MarketData(exchange, cache_ttl=...) if a different value is needed.
CANDLE_CACHE_TTL = 5

# claude code changed: new — fixes architecture audit finding M3.
# bot/engines/simulation.py previously defined these as its own local
# constants, disconnected from anything real (they're assumptions used
# only to simulate backtest fills, since there's no real exchange fill
# to read costs from during a backtest). Moved here so backtest cost
# assumptions live next to the live cache config, in one config package,
# instead of scattered across whichever file happens to need a number.

# Simulated slippage — price difference between signal and actual fill
# during backtesting. 0.0005 = 0.05%, a rough estimate for mid-cap crypto.
SLIPPAGE_RATE = 0.0005

# Simulated exchange fee — Binance standard taker fee, charged on both
# entry and exit during backtesting. 0.001 = 0.1%.
FEE_RATE = 0.001
