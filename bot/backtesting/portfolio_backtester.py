# ============================================================
# bot/backtesting/portfolio_backtester.py
# Portfolio Backtester — runs backtests across multiple coins
# and ranks performance using the StrategyScorer system.
#
# PURPOSE:
# Test the same regime-aware strategy system across multiple
# trading pairs simultaneously. This solves the core problem
# of statistical reliability — 7 coins × 100 trades = 700+
# trades total, giving the scorer's reliability factor a
# chance to reach 1.0 (full trust) on strong coins.
#
# KEY INSIGHT:
# If the strategy scores F on BTC but B+ on NEAR and B on SOL,
# the strategy is not broken — BTC is just the wrong asset for
# it right now. This engine reveals that distinction clearly.
#
# COMPATIBLE WITH YOUR PROJECT:
# - Calls your existing backtest() function directly
# - Uses your existing StrategyScorer
# - Fetches data via ccxt (same as MarketData)
# - No changes required to backtester.py
#
# USAGE:
#   from bot.backtesting.portfolio_backtester import PortfolioBacktester
#   portfolio = PortfolioBacktester(exchange)
#   results   = portfolio.run()
# ============================================================

import logging        # Structured logging throughout
import time           # Sleep between API calls to avoid rate limits
import pandas as pd   # DataFrame operations for OHLCV conversion
import ccxt           # Exchange connection for data fetching

# Import your existing backtest function — called directly, not wrapped
from bot.backtesting.backtester import backtest

# Import your existing strategy scorer — same one used on dashboard
from bot.engines.strategy_scorer import StrategyScorer

# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# PORTFOLIO BACKTESTER — MAIN CLASS
# ============================================================
class PortfolioBacktester:
    """
    Runs backtests across multiple trading pairs and ranks results.

    Flow:
    1. For each symbol: fetch OHLCV data from Binance
    2. Convert to pandas DataFrame (same format backtester expects)
    3. Call backtest() — your existing regime-aware engine
    4. Score results with StrategyScorer
    5. Rank all coins by total score
    6. Return ranked results with full breakdown

    The router inside backtest() will automatically route to
    MovingAverageStrategy or MeanReversionStrategy per regime —
    exactly as it does in your single-coin backtesting.
    """

    def __init__(
        self,
        exchange,                          # ccxt exchange instance (already authenticated)
        symbols: list = None,              # List of symbols to test — defaults to 7 coins
        timeframe: str = "1h",             # Candle timeframe
        candle_limit: int = 2000,          # Candles per symbol — 2000 = ~83 days on 1H
        initial_balance: float = 5000.0,   # Starting balance per backtest
        sleep_between: float = 1.0,        # Seconds to sleep between API calls (rate limit safety)
    ):
        # Store exchange instance — used for OHLCV fetching
        self.exchange = exchange

        # Default symbols if none provided
        # Selected for variety: large caps, mid caps, and your original pair
        self.symbols = symbols or [
            "BTC/USDT",    # Large cap — frequently ranging, hard to trend-trade
            "ETH/USDT",    # Large cap — moderate trend behaviour
            "NEAR/USDT",   # Your original pair — keep for comparison
            "SOL/USDT",    # Mid cap — often strong directional moves
            "BNB/USDT",    # Exchange token — moderate volatility
            "ADA/USDT",    # Alt — frequently ranging
            "XRP/USDT",   # Alt — good trend structure when trending
        ]

        self.timeframe       = timeframe        # e.g. "1h"
        self.candle_limit    = candle_limit      # How many candles to fetch per coin
        self.initial_balance = initial_balance   # Starting capital per backtest
        self.sleep_between   = sleep_between     # Rate limit protection

        # Storage for results — populated by run()
        self.all_results  = []     # List of per-coin result dicts
        self.ranked       = []     # Sorted by score (best first)
        self.errors       = {}     # Symbols that failed and why

        logger.info(
            f"[PortfolioBacktester] Initialised | "
            f"Symbols: {len(self.symbols)} | "
            f"Timeframe: {timeframe} | "
            f"Candles: {candle_limit} | "
            f"Balance: ${initial_balance}"
        )


    # ============================================================
    # RUN — MAIN PUBLIC METHOD
    # Loops through all symbols and runs the full pipeline
    # ============================================================
    def run(self) -> dict:
        """
        Runs backtests across all symbols and returns ranked results.

        Returns a dict with:
        - ranked:        list of per-coin results sorted by score
        - best_symbol:   the highest scoring coin
        - worst_symbol:  the lowest scoring coin
        - summary:       aggregate statistics across all coins
        - errors:        symbols that failed with error messages
        - coins_to_trade: list of coins with grade B or above
        - coins_to_avoid: list of coins with grade D or F
        """

        logger.info(
            f"[PortfolioBacktester] Starting portfolio backtest "
            f"across {len(self.symbols)} symbols..."
        )

        # Clear previous results
        self.all_results = []
        self.errors      = {}

        # Loop through each symbol
        for idx, symbol in enumerate(self.symbols):

            logger.info(
                f"[PortfolioBacktester] [{idx + 1}/{len(self.symbols)}] "
                f"Testing {symbol}..."
            )

            try:
                # ── Step 1: Fetch OHLCV data ──────────────────
                df = self._fetch_ohlcv(symbol)

                # Skip if data fetch failed
                if df is None or len(df) < 100:
                    error_msg = f"Insufficient data ({len(df) if df is not None else 0} candles)"
                    logger.warning(f"[PortfolioBacktester] {symbol} skipped: {error_msg}")
                    self.errors[symbol] = error_msg
                    continue

                # ── Step 2: Run backtest ───────────────────────
                # Calls your existing regime-aware backtest() function
                # Same function used on your dashboard
                result = backtest(df, initial_balance=self.initial_balance)

                # ── Step 3: Score the result ───────────────────
                scorer     = StrategyScorer(result)
                score_data = scorer.evaluate()

                # ── Step 4: Build per-coin summary ────────────
                coin_result = {
                    # Identity
                    "symbol":       symbol,                              # Trading pair

                    # Score summary
                    "total_score":  score_data["total_score"],           # 0-100 adjusted score
                    "raw_score":    score_data["raw_score"],             # Unadjusted score
                    "grade":        score_data["grade"],                 # Letter grade
                    "verdict":      score_data["verdict"],               # Plain English verdict
                    "reliability":  score_data["reliability_factor"],    # Sample size trust

                    # Core metrics
                    "total_trades": result.get("total_trades", 0),      # Trade count
                    "win_rate":     result.get("win_rate", 0),           # Win rate %
                    "profit_factor": result.get("profit_factor", 0),    # Profit factor
                    "avg_r":        score_data["metrics"]["avg_r"],      # Average R-Multiple
                    "expectancy_r": score_data["metrics"]["expectancy_r"],  # R-based expectancy
                    "max_drawdown": result.get("max_drawdown", 0),      # Max drawdown %
                    "final_balance": result.get("final_balance", self.initial_balance),
                    "net_profit":   round(
                        result.get("final_balance", self.initial_balance) - self.initial_balance, 2
                    ),                                                    # Net profit in dollars

                    # Regime intelligence
                    "regime_notes":    score_data.get("regime_notes", {}),
                    "structure_notes": score_data.get("structure_notes", {}),
                    "walk_forward":    score_data.get("walk_forward", {}),

                    # Action items
                    "suggestions":  score_data.get("suggestions", []),

                    # Full data for detailed analysis
                    "full_result":  result,       # Complete backtest output
                    "full_score":   score_data,   # Complete scorer output
                }

                # Add to results list
                self.all_results.append(coin_result)

                # Log per-coin summary
                logger.info(
                    f"[PortfolioBacktester] {symbol} complete | "
                    f"Score: {score_data['total_score']} ({score_data['grade']}) | "
                    f"Trades: {result.get('total_trades', 0)} | "
                    f"WR: {result.get('win_rate', 0)}% | "
                    f"Net: ${coin_result['net_profit']}"
                )

            except Exception as e:
                # Never let one coin crash the entire portfolio run
                error_msg = f"{type(e).__name__}: {e}"
                logger.error(f"[PortfolioBacktester] {symbol} FAILED: {error_msg}")
                self.errors[symbol] = error_msg    # Store error for reporting
                continue    # Move to next coin

            # Sleep between API calls to respect rate limits
            # Skips sleep after the last symbol
            if idx < len(self.symbols) - 1:
                time.sleep(self.sleep_between)

        # ── Sort by total score (best first) ──────────────────
        self.ranked = sorted(
            self.all_results,
            key=lambda x: x["total_score"],
            reverse=True    # Highest score first
        )

        # ── Build aggregate summary ───────────────────────────
        summary = self._build_summary()

        # ── Classify coins ────────────────────────────────────
        # Coins to trade: grade B+ or above (score >= 70)
        coins_to_trade = [
            r["symbol"] for r in self.ranked
            if r["total_score"] >= 70
        ]

        # Coins to avoid: grade D or F (score < 50)
        coins_to_avoid = [
            r["symbol"] for r in self.ranked
            if r["total_score"] < 50
        ]

        # Log the final ranking
        logger.info("=" * 55)
        logger.info("[PortfolioBacktester] PORTFOLIO RESULTS RANKED")
        logger.info("=" * 55)

        for rank, coin in enumerate(self.ranked, 1):
            logger.info(
                f"  #{rank} {coin['symbol']:12s} | "
                f"Score: {coin['total_score']:5.1f} ({coin['grade']}) | "
                f"Trades: {coin['total_trades']:3d} | "
                f"WR: {coin['win_rate']:5.1f}% | "
                f"Net: ${coin['net_profit']:8.2f}"
            )

        if coins_to_trade:
            logger.info(f"[PortfolioBacktester] TRADE THESE: {coins_to_trade}")
        if coins_to_avoid:
            logger.info(f"[PortfolioBacktester] AVOID THESE: {coins_to_avoid}")
        if self.errors:
            logger.warning(f"[PortfolioBacktester] ERRORS: {self.errors}")

        logger.info("=" * 55)

        # Return the complete portfolio result
        return {
            "ranked":         self.ranked,                          # All coins sorted by score
            "best_symbol":    self.ranked[0] if self.ranked else None,   # Highest scorer
            "worst_symbol":   self.ranked[-1] if self.ranked else None,  # Lowest scorer
            "coins_to_trade": coins_to_trade,                      # Grade B+ and above
            "coins_to_avoid": coins_to_avoid,                      # Grade D/F
            "summary":        summary,                              # Aggregate stats
            "errors":         self.errors,                         # Failed symbols
            "total_tested":   len(self.ranked),                    # Successful tests
            "total_failed":   len(self.errors),                    # Failed tests
        }


    # ============================================================
    # FETCH OHLCV — INTERNAL
    # Fetches candle data and converts to DataFrame
    # ============================================================
    def _fetch_ohlcv(self, symbol: str) -> pd.DataFrame:
        """
        Fetches OHLCV candles for a symbol and returns a DataFrame.

        Uses the same conversion as bot_runner.py's build_dataframe()
        so the output is identical to what backtester.py expects.

        Returns DataFrame or None on failure.
        """

        try:
            logger.debug(
                f"[PortfolioBacktester] Fetching {self.candle_limit} "
                f"{self.timeframe} candles for {symbol}..."
            )

            # Fetch raw candles from exchange
            # Returns list of [timestamp, open, high, low, close, volume]
            candles = self.exchange.fetch_ohlcv(
                symbol    = symbol,
                timeframe = self.timeframe,
                limit     = self.candle_limit,
            )

            # Guard: check we received data
            if not candles or len(candles) == 0:
                logger.warning(f"[PortfolioBacktester] Empty candle response for {symbol}")
                return None

            # Convert list of lists to named DataFrame columns
            df = pd.DataFrame(
                candles,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            # Convert millisecond timestamp to readable datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

            # Set DatetimeIndex — required by RegimeDetector for resampling
            df.set_index("timestamp", inplace=True)

            # Force all price columns to float — removes any string artifacts
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            # Drop any rows with NaN price data
            df.dropna(subset=["open", "high", "low", "close"], inplace=True)

            # Reset index after cleaning — keeps index sequential and clean
            df.reset_index(inplace=True)

            # Set DatetimeIndex again after reset
            df.set_index("timestamp", inplace=True)

            logger.debug(
                f"[PortfolioBacktester] {symbol}: {len(df)} candles ready"
            )

            return df    # Return clean DataFrame

        except ccxt.NetworkError as e:
            logger.error(f"[PortfolioBacktester] Network error fetching {symbol}: {e}")
            return None

        except ccxt.RateLimitExceeded as e:
            # Rate limited — wait longer and try once more
            logger.warning(f"[PortfolioBacktester] Rate limited on {symbol}: {e}")
            time.sleep(30)    # 30 second hard backoff

            try:
                # One retry after rate limit cooldown
                candles = self.exchange.fetch_ohlcv(
                    symbol    = symbol,
                    timeframe = self.timeframe,
                    limit     = self.candle_limit,
                )
                # Rebuild DataFrame (same logic as above)
                df = pd.DataFrame(
                    candles,
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                df.reset_index(inplace=True)
                df.set_index("timestamp", inplace=True)
                return df

            except Exception:
                return None    # Give up after one retry

        except Exception as e:
            logger.error(
                f"[PortfolioBacktester] Unexpected error fetching {symbol}: "
                f"{type(e).__name__}: {e}"
            )
            return None


    # ============================================================
    # BUILD SUMMARY — INTERNAL
    # Calculates aggregate statistics across all tested coins
    # ============================================================
    def _build_summary(self) -> dict:
        """
        Calculates portfolio-level aggregate statistics.
        Weighted by trade count so coins with more trades
        contribute more to the portfolio average.
        """

        if not self.all_results:
            return {"available": False, "message": "No results to summarise"}

        total_coins   = len(self.all_results)      # Number of coins tested
        total_trades  = sum(r["total_trades"] for r in self.all_results)
        total_profit  = sum(r["net_profit"] for r in self.all_results)

        # Weighted average score (weighted by trade count)
        # Coins with more trades have more influence on the average
        # This is more accurate than a simple average
        if total_trades > 0:
            weighted_score = sum(
                r["total_score"] * r["total_trades"]
                for r in self.all_results
            ) / total_trades
        else:
            weighted_score = 0.0

        # Simple average win rate across all coins
        avg_win_rate = round(
            sum(r["win_rate"] for r in self.all_results) / total_coins, 2
        ) if total_coins > 0 else 0.0

        # Simple average R across all coins
        avg_r = round(
            sum(r["avg_r"] for r in self.all_results) / total_coins, 4
        ) if total_coins > 0 else 0.0

        # Count coins by grade tier
        grade_distribution = {"A+": 0, "A": 0, "B+": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for r in self.all_results:
            grade = r["grade"]
            if grade in grade_distribution:
                grade_distribution[grade] += 1

        # Best performing coin
        best  = max(self.all_results, key=lambda r: r["total_score"])
        worst = min(self.all_results, key=lambda r: r["total_score"])

        # Count coins where strategy is profitable
        profitable_coins = sum(
            1 for r in self.all_results if r["net_profit"] > 0
        )

        return {
            "available":          True,
            "total_coins_tested": total_coins,
            "total_trades":       total_trades,          # Combined trade count
            "total_net_profit":   round(total_profit, 2),
            "weighted_avg_score": round(weighted_score, 1),
            "avg_win_rate":       avg_win_rate,
            "avg_r":              avg_r,
            "profitable_coins":   profitable_coins,      # Coins with positive P&L
            "losing_coins":       total_coins - profitable_coins,
            "best_coin":          best["symbol"],
            "best_score":         best["total_score"],
            "worst_coin":         worst["symbol"],
            "worst_score":        worst["total_score"],
            "grade_distribution": grade_distribution,    # How many coins per grade
        }


    # ============================================================
    # PRINT REPORT — PUBLIC UTILITY
    # Prints a formatted summary table to the terminal
    # ============================================================
    def print_report(self):
        """
        Prints a clean formatted ranking table to the terminal.
        Call this after run() to see results at a glance.
        """

        if not self.ranked:
            print("[PortfolioBacktester] No results to display.")
            return

        # Print header
        print()
        print("=" * 75)
        print("PORTFOLIO BACKTEST RESULTS")
        print("=" * 75)
        print(
            f"{'#':<3} {'Symbol':<12} {'Score':<7} {'Grade':<6} "
            f"{'Trades':<8} {'WR%':<7} {'Avg R':<8} {'Net P&L':<10} {'Verdict'}"
        )
        print("-" * 75)

        # Print each coin
        for rank, coin in enumerate(self.ranked, 1):
            print(
                f"{rank:<3} "
                f"{coin['symbol']:<12} "
                f"{coin['total_score']:<7.1f} "
                f"{coin['grade']:<6} "
                f"{coin['total_trades']:<8} "
                f"{coin['win_rate']:<7.1f} "
                f"{coin['avg_r']:<8.4f} "
                f"${coin['net_profit']:<9.2f} "
                f"{coin['verdict'][:30]}"
            )

        print("=" * 75)

        # Print summary
        if self.all_results:
            summary = self._build_summary()
            print(f"\nCombined trades:  {summary['total_trades']}")
            print(f"Combined P&L:     ${summary['total_net_profit']}")
            print(f"Weighted score:   {summary['weighted_avg_score']}")
            print(f"Profitable coins: {summary['profitable_coins']}/{summary['total_coins_tested']}")

        # Print recommendations
        coins_to_trade = [r["symbol"] for r in self.ranked if r["total_score"] >= 70]
        coins_to_avoid = [r["symbol"] for r in self.ranked if r["total_score"] < 50]

        if coins_to_trade:
            print(f"\n✓ TRADE THESE:  {', '.join(coins_to_trade)}")
        if coins_to_avoid:
            print(f"✗ AVOID THESE:  {', '.join(coins_to_avoid)}")

        if self.errors:
            print(f"\n⚠ FAILED: {list(self.errors.keys())}")

        print()