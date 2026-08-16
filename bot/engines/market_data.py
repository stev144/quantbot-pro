# ============================================================
# bot/engines/market_data.py (FIXED VERSION)
# Market Data Layer — optimized logging, configurable cache
# ============================================================

import logging
import time
import ccxt

from bot.config.execution_costs import CANDLE_CACHE_TTL

# Create a logger for this module
logger = logging.getLogger(__name__)


class MarketData:
    """
    Responsible for fetching and caching market data.
    Optimized for speed with minimal logging overhead.
    """

    def __init__(self, exchange, cache_ttl=None):
        # Store the exchange instance passed in from bot_runner
        self.exchange = exchange

        # Use provided cache_ttl or fall back to config default
        self.cache_ttl = cache_ttl or CANDLE_CACHE_TTL

        # Cache stores the last fetched candles per symbol+timeframe
        # Format: { "NEAR/USDT_1h": [ [timestamp, o, h, l, c, v], ... ] }
        self.cache = {}

        # Timestamp of last fetch per symbol
        self.last_fetch_time = {}


    # ============================================================
    # GET OHLCV CANDLES
    # ============================================================
    def get_ohlcv(self, symbol, timeframe="1h", limit=100):
        """
        Fetches candles for the given symbol and timeframe.
        Uses cache to avoid redundant API calls.
        Returns list of candles or None on failure.
        """

        # Build a unique cache key
        cache_key = f"{symbol}_{timeframe}"
        now = time.time()

        # Check if we have a fresh cache entry
        last_fetch = self.last_fetch_time.get(cache_key, 0)

        if cache_key in self.cache and (now - last_fetch) < self.cache_ttl:
            # Return from cache — no API call needed
            return self.cache[cache_key]

        # Cache is stale — fetch fresh candles from exchange
        try:
            # Minimal logging — only on fetch, not on cache hit
            logger.debug(f"[MarketData] Fetching candles | {symbol} | {timeframe}")

            # Call ccxt unified method
            candles = self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit
            )

            # Validate we received data
            if not candles or len(candles) == 0:
                logger.warning(f"[MarketData] Empty response for {symbol}")
                return None

            # Store in cache
            self.cache[cache_key] = candles
            self.last_fetch_time[cache_key] = now

            return candles

        except ccxt.NetworkError as e:
            # Network issue — return stale cache if available
            logger.debug(f"[MarketData] Network error fetching {symbol}: {e}")
            return self.cache.get(cache_key)

        except Exception as e:
            # Any other error
            logger.error(f"[MarketData] Fetch failed for {symbol}: {type(e).__name__}")
            return None


    # ============================================================
    # GET CURRENT PRICE
    # ============================================================
    def get_price(self, symbol):
        """
        Fetches the latest market price for a symbol.
        Returns float price or None on failure.
        Minimal logging for speed.
        """ 

        try:
            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker.get("last")

            if price is None or price <= 0:
                logger.debug(f"[MarketData] Invalid price for {symbol}: {price}")
                return None

            return float(price)

        except ccxt.NetworkError as e:
            logger.debug(f"[MarketData] Network error fetching price: {e}")
            return None

        except Exception as e:
            logger.error(f"[MarketData] Price fetch failed: {type(e).__name__}")
            return None


    # ============================================================
    # GET DYNAMIC ACCOUNT BALANCE
    # ============================================================
    def get_balance(self):
        """
        Returns the current free USDT balance from the exchange.
        Called before every new position.
        """

        try:
            balances = self.exchange.fetch_balance()
            usdt_free = balances.get("USDT", {}).get("free", 0.0)

            # Log balance only on significant changes (info level)
            logger.info(f"[MarketData] Balance: ${usdt_free:.2f}")

            return float(usdt_free)

        except ccxt.AuthenticationError as e:
            logger.critical(f"[MarketData] Auth failed — check API keys: {e}")
            raise

        except Exception as e:
            logger.error(f"[MarketData] Balance fetch failed: {type(e).__name__}")
            return 0.0