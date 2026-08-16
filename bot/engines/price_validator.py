# ============================================================
# bot/engines/price_validator.py
# Cross-Exchange Price Validator — sanity-checks Binance prices
# against a second exchange before the bot trades on them.
#
# Why this exists (architecture audit issue 6):
# Binance is the bot's sole price source everywhere else in this
# codebase. A bad tick, a stale cache, or a thin/manipulated order
# book on Binance would flow straight into regime detection and
# signal generation with nothing independent to catch it. This
# module fetches the same symbol's live price from a second,
# unrelated exchange and flags candles where the two disagree by
# more than a configured threshold — Binance remains the primary
# data source everywhere; this is a read-only sanity check on top
# of it, not a replacement.
# ============================================================

import logging
import ccxt

logger = logging.getLogger(__name__)

# Maximum allowed deviation between the primary (Binance) price and the
# secondary exchange's price before a candle is flagged suspicious.
MAX_PRICE_DEVIATION_PCT = 0.5


class PriceCrossChecker:
    """
    Sanity-checks a primary-exchange (Binance) price against a second,
    independent exchange (Kraken by default). Never places orders or reads
    balances — only calls the secondary exchange's public fetch_ticker(),
    so no API keys are needed for it.
    """

    def __init__(self, secondary_exchange_id="kraken"):
        exchange_class = getattr(ccxt, secondary_exchange_id)
        self.secondary_exchange = exchange_class()
        self.secondary_exchange_id = secondary_exchange_id

    # ============================================================
    # CHECK
    # Compares a primary-exchange price against the secondary
    # exchange's live price for the same symbol.
    # ============================================================
    def check(self, symbol, primary_price):
        """
        Returns:
            {
                "suspicious":         bool,
                "primary_price":      float,
                "secondary_price":    float or None,  # None if fetch failed
                "deviation_pct":      float or None,
                "secondary_exchange": str,
            }

        A failed secondary fetch (network error, symbol not listed there,
        etc.) is NOT treated as suspicious — there's nothing to compare
        against. It's logged as a warning and returns suspicious=False;
        blocking every trade because the secondary exchange is flaky would
        trade a real safety net for a worse one (bot uptime on its primary,
        working data source).
        """
        result = {
            "suspicious":         False,
            "primary_price":      primary_price,
            "secondary_price":    None,
            "deviation_pct":      None,
            "secondary_exchange": self.secondary_exchange_id,
        }

        try:
            ticker = self.secondary_exchange.fetch_ticker(symbol)
            secondary_price = ticker.get("last")
        except Exception as e:
            logger.warning(
                f"[PriceCrossChecker] Could not fetch {symbol} from "
                f"{self.secondary_exchange_id}: {type(e).__name__}: {e}. "
                f"Skipping cross-check for this candle — Binance price used as-is."
            )
            return result

        if not secondary_price or secondary_price <= 0:
            logger.warning(
                f"[PriceCrossChecker] Invalid {symbol} price from "
                f"{self.secondary_exchange_id}: {secondary_price}. "
                f"Skipping cross-check for this candle."
            )
            return result

        deviation_pct = abs(primary_price - secondary_price) / secondary_price * 100
        result["secondary_price"] = secondary_price
        result["deviation_pct"]   = deviation_pct

        if deviation_pct > MAX_PRICE_DEVIATION_PCT:
            result["suspicious"] = True
            logger.warning(
                f"[PriceCrossChecker] SUSPICIOUS price for {symbol} | "
                f"Binance: {primary_price} | {self.secondary_exchange_id}: "
                f"{secondary_price} | Deviation: {deviation_pct:.3f}% "
                f"(threshold: {MAX_PRICE_DEVIATION_PCT}%)"
            )
        else:
            logger.debug(
                f"[PriceCrossChecker] {symbol} price OK | "
                f"Binance: {primary_price} | {self.secondary_exchange_id}: "
                f"{secondary_price} | Deviation: {deviation_pct:.3f}%"
            )

        return result
