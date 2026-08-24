import os  # Read environment variables and paths.
import re  # Validate and normalize symbols.
import time  # Sleep for rate-limit backoff and cache timing.
import json  # Parse JSON payloads from Binance and aiohttp.
import logging  # Emit useful debug and error logs.
import pickle  # Store cached DataFrames on disk.
from datetime import datetime, timezone  # Work with timestamps and UTC time.
from typing import Optional, Dict, Any, List  # Type hints for clarity.

import requests  # Synchronous HTTP client for Binance REST API.
import pandas as pd  # DataFrame handling for OHLCV data.
from requests.adapters import HTTPAdapter  # Connection pooling and retries.
from urllib3.util import Retry  # Retry strategy for transient failures.

try:
    import aiohttp  # Async HTTP client for async endpoints.
    import asyncio  # Async sleep and timeout handling.
except ImportError:
    aiohttp = None  # Keep async features optional.
    asyncio = None  # Keep async features optional.

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("data_fetcher")  # Create a dedicated module logger.
if not LOGGER.handlers:  # Prevent duplicate handlers when re-imported.
    LOGGER.setLevel(logging.INFO)  # Use INFO by default.
    _handler = logging.StreamHandler()  # Print logs to console.
    _formatter = logging.Formatter(  # Create readable log format.
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",  # Log pattern.
        datefmt="%Y-%m-%d %H:%M:%S",  # Human-friendly time format.
    )
    _handler.setFormatter(_formatter)  # Attach formatter to handler.
    LOGGER.addHandler(_handler)  # Attach handler to logger.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")  # Binance REST base URL.
MAX_API_LIMIT = 1000  # Binance candles per request ceiling.
DEFAULT_TIMEOUT = (3.05, 10)  # FIX #3: Reduced read timeout from 15s -> 10s to limit per-request blocking.
DEFAULT_CACHE_TTL_SECONDS = int(os.environ.get("DATA_FETCHER_CACHE_TTL", "300"))  # Cache expiry in seconds.

# FIX #1/#2: Global retry and pagination safety caps to prevent infinite loops.
MAX_RATE_LIMIT_RETRIES = 5    # Maximum number of 429 retries before giving up.
MAX_PAGINATION_PAGES = 200     # Hard page ceiling for both forward and backward pagination.

VALID_INTERVALS = {  # Supported Binance intervals.
    "1s", "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}
SYMBOL_REGEX = re.compile(r"^[A-Z0-9]{5,20}$")  # After normalization, Binance symbols are compact.

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")  # Store cache files next to the module.
os.makedirs(CACHE_DIR, exist_ok=True)  # Ensure cache folder exists.

# ---------------------------------------------------------------------------
# Validation and normalization helpers
# ---------------------------------------------------------------------------

def _normalize_symbol(symbol: str) -> str:
    """Convert BTC/USDT, BTC-USDT, btcusdt into BTCUSDT."""  # Normalize symbols for Binance.
    if not isinstance(symbol, str):  # Reject non-string values.
        return ""  # Return empty string when invalid.
    cleaned = symbol.upper().strip()  # Uppercase and trim whitespace.
    cleaned = cleaned.replace("/", "")  # Remove slash separators.
    cleaned = cleaned.replace("-", "")  # Remove dash separators.
    cleaned = cleaned.replace(" ", "")  # Remove spaces.
    cleaned = cleaned.replace("_", "")  # Remove underscores.
    return cleaned  # Return the standardized symbol.

def _validate_symbol(symbol: str) -> bool:
    """Check whether a symbol becomes a valid Binance-style string."""  # Validate compact symbol.
    normalized = _normalize_symbol(symbol)  # Normalize first.
    return bool(normalized and SYMBOL_REGEX.match(normalized))  # Return validation result.

def _validate_interval(interval: str) -> bool:
    """Check that the requested candle interval is supported."""  # Validate interval.
    return interval in VALID_INTERVALS  # Return membership result.

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> str:
    """Build a safe path for a cache file."""  # Convert cache key into a filename.
    safe_key = re.sub(r"[^A-Za-z0-9_\-\.]", "_", key)  # Remove unsafe filename characters.
    return os.path.join(CACHE_DIR, f"{safe_key}.pkl")  # Return the pickle path.

def _load_cache(key: str) -> Optional[pd.DataFrame]:
    """Load a cached DataFrame if it exists and has not expired."""  # Read cached payload.
    path = _cache_path(key)  # Resolve cache file path.
    if not os.path.exists(path):  # Stop early when file is missing.
        return None  # No cache available.
    try:
        # FIX #5: Wrap pickle.load in a strict try/except to guard against
        # corrupted or partially written cache files blocking the thread.
        with open(path, "rb") as f:  # Open cache file safely.
            payload = pickle.load(f)  # Unpickle the payload.
        if isinstance(payload, dict) and "df" in payload and "expires_at" in payload:  # Check new cache format.
            if time.time() > float(payload["expires_at"]):  # Check expiration.
                try:
                    os.remove(path)  # Remove stale file.
                except Exception:
                    pass  # Ignore cleanup errors.
                return None  # Return nothing for expired cache.
            df = payload["df"]  # Extract DataFrame.
            if isinstance(df, pd.DataFrame):  # Confirm valid object.
                LOGGER.info(f"Cache hit for key={key}")  # Log successful cache read.
                return df  # Return cached DataFrame.
            return None  # Reject malformed payloads.
        if isinstance(payload, pd.DataFrame):  # Support older cache format.
            LOGGER.info(f"Cache hit for key={key}")  # Log successful cache read.
            return payload  # Return DataFrame directly.
    except (pickle.UnpicklingError, EOFError, AttributeError, ImportError) as e:
        # FIX #5: Catch specific pickle corruption errors and delete the bad file
        # rather than silently swallowing the error or blocking indefinitely.
        LOGGER.warning(f"Corrupted cache file for key={key}, removing: {e}")
        try:
            os.remove(path)  # Delete corrupted cache file.
        except Exception:
            pass  # Ignore removal errors.
    except Exception as e:  # Catch any remaining read failures.
        LOGGER.warning(f"Failed to load cache for key={key}: {e}")  # Log and continue.
    return None  # Return nothing when cache cannot be used.

def _save_cache(key: str, df: pd.DataFrame, ttl_seconds: int) -> None:
    """Save a DataFrame with a TTL to disk."""  # Persist cache payload.
    path = _cache_path(key)  # Resolve target path.
    try:
        payload = {  # Prepare metadata payload.
            "expires_at": time.time() + float(ttl_seconds),  # Store expiry time.
            "saved_at": time.time(),  # Store save time.
            "df": df.copy(),  # Store a copy of the DataFrame.
        }
        with open(path, "wb") as f:  # Open file for writing.
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)  # Write payload efficiently.
        LOGGER.info(f"Cache saved for key={key}")  # Log success.
    except Exception as e:  # Catch disk write failures.
        LOGGER.warning(f"Failed to save cache for key={key}: {e}")  # Log warning.

# ---------------------------------------------------------------------------
# Requests session and rate limit handling
# ---------------------------------------------------------------------------

def _create_production_session() -> requests.Session:
    """Create a resilient session with retries and connection pooling."""  # Build shared session.
    session = requests.Session()  # Create one reusable HTTP session.
    retry_strategy = Retry(  # Configure retry policy.
        total=3,              # FIX #3: Reduced from 5 -> 3 retries to limit max blocking time.
        connect=3,            # FIX #3: Reduced from 5 -> 3 to match.
        read=3,               # FIX #3: Reduced from 5 -> 3 to match.
        backoff_factor=1,     # Increase delay exponentially between retries.
        status_forcelist=[500, 502, 503, 504],  # FIX #1: Removed 429 from auto-retry list.
        # 429s are now handled manually with a counter so we can enforce MAX_RATE_LIMIT_RETRIES.
        allowed_methods=["GET"],  # Only retry safe requests.
        raise_on_status=False,    # Let us handle status manually.
    )
    adapter = HTTPAdapter(  # Create transport adapter.
        pool_connections=10,  # Limit TCP pool size.
        pool_maxsize=10,      # Limit max pooled connections.
        max_retries=retry_strategy,  # Attach retry policy.
    )
    session.mount("https://", adapter)  # Mount adapter for HTTPS.
    session.mount("http://", adapter)   # Mount adapter for HTTP.
    return session  # Return configured session.

_SESSION = _create_production_session()  # Create a shared session for sync calls.

def _handle_rate_limits(response: requests.Response) -> bool:
    """Handle Binance 429 and 418 responses. Returns True if caller may retry."""
    status = response.status_code  # Read status code.
    if status == 418:  # Hard ban.
        LOGGER.error("IP BANNED by Binance (HTTP 418). Immediate manual intervention required.")
        return False  # Do not retry.
    if status == 429:  # Rate limit hit.
        retry_after = response.headers.get("Retry-After", "5")  # Read retry header if available.
        try:
            delay = int(retry_after)  # Convert to integer seconds.
        except ValueError:
            delay = 5  # Use a safe default.
        LOGGER.warning(f"Rate limit hit (429). Sleeping for {delay} seconds...")
        time.sleep(delay)  # Sleep before retry.
        return True  # Caller may retry.
    return False  # No special handling needed.

def _monitor_weight(response: requests.Response) -> None:
    """Watch Binance weight usage and cool down if it gets too high."""  # Protect IP reputation.
    weight = response.headers.get("X-MBX-USED-WEIGHT-1M")  # Read Binance weight header.
    if weight:  # Only act when header exists.
        try:
            used = int(weight)  # Convert header to integer.
            if used > 1000:  # If near the limit.
                LOGGER.warning(f"Binance weight high ({used}/1200). Cooling down for 5 seconds.")
                time.sleep(5)  # Cool down briefly.
        except ValueError:
            pass  # Ignore malformed header values.

# ---------------------------------------------------------------------------
# Small utility endpoints
# ---------------------------------------------------------------------------

def get_server_time() -> int:
    """Fetch Binance server time in milliseconds."""  # Useful for sync checks.
    url = f"{BINANCE_BASE_URL}/api/v3/time"  # Server time endpoint.
    try:
        response = _SESSION.get(url, timeout=DEFAULT_TIMEOUT)  # Send request.
        if response.status_code != 200:  # Check response.
            LOGGER.error(f"API ERROR [Status {response.status_code}] for server time: {response.text}")
            return 0  # Return fallback.
        data = response.json()  # Parse JSON.
        return int(data.get("serverTime", 0))  # Return time value.
    except Exception as e:
        LOGGER.error(f"Failed to fetch server time: {e}")  # Log failure.
        return 0  # Return fallback.

def get_24hr_ticker(symbol: str) -> Dict[str, Any]:
    """Fetch Binance 24h ticker stats for a symbol."""  # Helper for dashboard stats.
    normalized = _normalize_symbol(symbol)  # Normalize symbol.
    if not _validate_symbol(normalized):  # Validate input.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return {}  # Return empty data.
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"  # Ticker endpoint.
    params = {"symbol": normalized}  # Request a specific symbol.
    try:
        response = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)  # Send request.
        _monitor_weight(response)  # Monitor API usage.
        if response.status_code in (429, 418):  # Handle limits/bans.
            if not _handle_rate_limits(response):  # Ask whether to retry.
                return {}  # Stop on hard ban.
            # FIX #4: Retry exactly once after rate-limit sleep, consistent with all other callers.
            response = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        if response.status_code != 200:  # Check for server errors.
            LOGGER.error(f"API ERROR [Status {response.status_code}] for 24hr ticker: {response.text}")
            return {}  # Return empty result.
        data = response.json()  # Parse JSON response.
        return data if isinstance(data, dict) else {}  # Return dict only.
    except Exception as e:
        LOGGER.error(f"Failed to fetch 24hr ticker for {symbol}: {e}")
        return {}  # Return empty result.

# ---------------------------------------------------------------------------
# Price fetch
# ---------------------------------------------------------------------------

def get_price(symbol: str) -> float:
    """Fetch the current price for a symbol."""  # Simple latest price helper.
    normalized = _normalize_symbol(symbol)  # Normalize symbol first.
    if not _validate_symbol(normalized):  # Validate symbol.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return 0.0  # Return fallback.
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"  # Price endpoint.
    params = {"symbol": normalized}  # Request one symbol.

    # FIX #1: Replaced infinite `while True` with a bounded retry counter.
    # Previously, repeated 429 responses could loop forever. Now we give up
    # after MAX_RATE_LIMIT_RETRIES attempts and return 0.0 safely.
    rate_limit_attempts = 0

    try:
        while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded retry loop.
            response = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)  # Send request.
            _monitor_weight(response)  # Watch used weight.
            if response.status_code in (429, 418):  # Handle throttling/bans.
                if not _handle_rate_limits(response):  # Decide whether retry is safe.
                    return 0.0  # Stop on hard ban.
                rate_limit_attempts += 1  # Count this retry attempt.
                if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:  # Give up if exhausted.
                    LOGGER.error(
                        f"get_price: exceeded {MAX_RATE_LIMIT_RETRIES} rate-limit retries "
                        f"for {symbol}. Giving up."
                    )
                    return 0.0  # Return fallback instead of looping forever.
                continue  # Retry after sleep.
            if response.status_code != 200:  # Reject non-200 responses.
                LOGGER.error(f"API ERROR [Status {response.status_code}] for price fetch: {response.text}")
                return 0.0  # Return fallback.
            data = response.json()  # Parse JSON.
            if not isinstance(data, dict) or "price" not in data:  # Validate payload.
                LOGGER.error(f"INVALID DATA FORMAT for price fetch: {data}")
                return 0.0  # Return fallback.
            return float(data["price"])  # Return current price.
        # Should not reach here but return safely if loop exits unexpectedly.
        return 0.0
    except requests.exceptions.RequestException as e:  # Catch network issues.
        LOGGER.error(f"NETWORK ERROR: Failed to contact Binance endpoint for {symbol}: {e}")
        return 0.0  # Return fallback.
    except (ValueError, TypeError, KeyError) as e:  # Catch parsing issues.
        LOGGER.error(f"PARSING ERROR: Payload translation corrupted for {symbol}: {e}")
        return 0.0  # Return fallback. 

# ---------------------------------------------------------------------------
# Core OHLCV DataFrame builder
# ---------------------------------------------------------------------------

def _build_ohlcv_dataframe(raw_data: List[List[Any]]) -> pd.DataFrame:
    """Convert raw Binance klines into a clean OHLCV DataFrame."""  # Transform candles.
    if not raw_data:  # Stop if no candles.
        LOGGER.warning("EMPTY DATA in _build_ohlcv_dataframe")
        return pd.DataFrame()  # Return empty DataFrame.
    df = pd.DataFrame(  # Build the table.
        raw_data,  # Use raw response rows.
        columns=[  # Binance kline schema.
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "qav", "num_trades",
            "taker_base_vol", "taker_quote_vol", "ignore",
        ],
    )
    df.drop_duplicates(subset=["timestamp"], inplace=True)  # Remove duplicate candles.
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)  # Convert to UTC datetime.
    for col in ["open", "high", "low", "close", "volume", "qav", "taker_base_vol"]:  # claude code changed: was only open/high/low/close/volume — Phase 2B Step 1, qav/taker_base_vol are also numeric strings in Binance's raw response and need the same coercion; num_trades arrives as a real int already and needs none.
        df[col] = pd.to_numeric(df[col], errors="coerce")  # Convert and coerce invalid values.
    df.set_index("timestamp", inplace=True)  # Use timestamp as the index.
    df.sort_index(inplace=True)  # Sort candles chronologically.
    df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)  # Remove invalid rows.
    return df  # Return cleaned DataFrame.

# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def _fetch_klines_backward(
    symbol: str,
    interval: str,
    total_candles: int,
) -> List[List[Any]]:
    """Fetch the most recent candles by paging backwards from the current end."""
    url = f"{BINANCE_BASE_URL}/api/v3/klines"  # Binance candles endpoint.
    all_raw_data: List[List[Any]] = []  # Accumulate all candles here.
    remaining = max(1, int(total_candles))  # Keep at least one candle.
    current_end_time: Optional[int] = None  # Start from the latest possible time.
    seen_timestamps = set()  # Prevent duplicate loops.

    # FIX #2: Added hard page ceiling. Previously the loop had no page limit,
    # so a large total_candles or a slow Binance response could paginate
    # indefinitely, blocking the server thread for many minutes.
    page_count = 0

    while remaining > 0:  # Continue until enough candles are collected.
        if page_count >= MAX_PAGINATION_PAGES:  # Hard stop after page ceiling.
            LOGGER.warning(
                f"_fetch_klines_backward: reached page limit ({MAX_PAGINATION_PAGES}). "
                f"Stopping pagination early."
            )
            break

        current_limit = min(remaining, MAX_API_LIMIT)  # Respect Binance hard limit.
        params: Dict[str, Any] = {  # Build request params.
            "symbol": _normalize_symbol(symbol),  # Send normalized symbol.
            "interval": interval,  # Send interval.
            "limit": current_limit,  # Ask for requested chunk.
        }
        if current_end_time is not None:  # Include paging cursor when available.
            params["endTime"] = current_end_time  # Page backwards.

        # FIX #1: Track rate-limit retries per page so a sustained 429 storm
        # cannot loop this pagination forever.
        rate_limit_attempts = 0

        try:
            while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded per-page retry.
                response = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
                _monitor_weight(response)  # Track Binance weight usage.
                if response.status_code in (429, 418):  # Handle throttling.
                    if not _handle_rate_limits(response):  # Stop on hard ban.
                        return all_raw_data  # Return whatever was collected.
                    rate_limit_attempts += 1
                    if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:
                        LOGGER.error(
                            f"_fetch_klines_backward: exceeded rate-limit retries on page "
                            f"{page_count}. Stopping."
                        )
                        return all_raw_data  # Return partial data safely.
                    continue  # Retry same page.
                break  # Exit per-page retry loop on a non-429 response.

            if response.status_code != 200:  # Abort on server-side errors.
                LOGGER.error(f"API ERROR [Status {response.status_code}]: {response.text}")
                break  # Exit pagination.
            data = response.json()  # Parse JSON response.
            if not isinstance(data, list):  # Validate schema.
                LOGGER.error(f"INVALID DATA: {data}")
                break  # Exit pagination.
            if len(data) == 0:  # Stop when no more data exists.
                break  # Exit pagination.

            new_rows = 0  # Count unique rows in this page.
            for row in data:  # Scan each row.
                ts = row[0]  # Read candle timestamp.
                if ts not in seen_timestamps:  # Skip duplicates.
                    all_raw_data.append(row)  # Keep new candle.
                    seen_timestamps.add(ts)  # Remember candle timestamp.
                    new_rows += 1  # Count new candle.
            remaining -= new_rows  # Reduce remaining target count.
            first_timestamp = int(data[0][0])  # Oldest candle timestamp in this page.
            current_end_time = first_timestamp - 1  # Page to older candles next time.
            page_count += 1  # Increment page counter.
            if len(data) < current_limit:  # Stop if the API gave fewer than requested.
                break  # No more pages likely available.
            time.sleep(0.08)  # Small pacing delay to protect the API.
        except requests.exceptions.RequestException as e:  # Catch HTTP/network issues.
            LOGGER.error(f"NETWORK ERROR during backward pagination: {e}")
            break  # Stop fetching.
        except Exception as e:  # Catch any unexpected bug.
            LOGGER.error(f"UNEXPECTED EXTRACTION ERROR during backward pagination: {e}")
            break  # Stop fetching.
    return all_raw_data  # Return raw candles.

def _fetch_klines_forward(
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    max_candles: int,
) -> List[List[Any]]:
    """Fetch candles forward from startTime to endTime for date-range queries."""
    url = f"{BINANCE_BASE_URL}/api/v3/klines"  # Binance candles endpoint.
    all_raw_data: List[List[Any]] = []  # Accumulate candles.
    remaining = max(1, int(max_candles))  # Prevent invalid ranges.
    current_start = start_time_ms  # Start paging from the requested start.
    seen_timestamps = set()  # Prevent duplicate candles.

    # FIX #2: Same hard page ceiling applied to forward pagination.
    page_count = 0

    while remaining > 0 and current_start <= end_time_ms:  # Continue while range is valid.
        if page_count >= MAX_PAGINATION_PAGES:  # Hard stop after page ceiling.
            LOGGER.warning(
                f"_fetch_klines_forward: reached page limit ({MAX_PAGINATION_PAGES}). "
                f"Stopping pagination early."
            )
            break

        current_limit = min(remaining, MAX_API_LIMIT)  # Respect Binance candle cap.
        params: Dict[str, Any] = {  # Build query params.
            "symbol": _normalize_symbol(symbol),  # Normalized symbol.
            "interval": interval,  # Requested interval.
            "limit": current_limit,  # Batch size.
            "startTime": current_start,  # Forward cursor.
            "endTime": end_time_ms,  # Range ceiling.
        }

        # FIX #1: Bounded rate-limit retry loop per page.
        rate_limit_attempts = 0

        try:
            while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded per-page retry.
                response = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
                _monitor_weight(response)  # Watch API usage.
                if response.status_code in (429, 418):  # Handle throttling.
                    if not _handle_rate_limits(response):  # Stop on ban.
                        return all_raw_data  # Return partial data.
                    rate_limit_attempts += 1
                    if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:
                        LOGGER.error(
                            f"_fetch_klines_forward: exceeded rate-limit retries on page "
                            f"{page_count}. Stopping."
                        )
                        return all_raw_data  # Return partial data safely.
                    continue  # Retry same page.
                break  # Exit per-page retry loop on non-429.

            if response.status_code != 200:  # Abort on server errors.
                LOGGER.error(f"API ERROR [Status {response.status_code}]: {response.text}")
                break  # Exit paging.
            data = response.json()  # Parse JSON.
            if not isinstance(data, list):  # Validate schema.
                LOGGER.error(f"INVALID DATA: {data}")
                break  # Exit paging.
            if len(data) == 0:  # Stop when no candles returned.
                break  # Exit paging.

            new_rows = 0  # Count unique candles.
            for row in data:  # Iterate raw rows.
                ts = row[0]  # Read timestamp.
                if ts not in seen_timestamps:  # Filter duplicates.
                    all_raw_data.append(row)  # Store new candle.
                    seen_timestamps.add(ts)  # Remember it.
                    new_rows += 1  # Count it.
            remaining -= new_rows  # Update remaining count.
            last_timestamp = int(data[-1][0])  # Read newest candle in this page.
            current_start = last_timestamp + 1  # Move cursor forward.
            page_count += 1  # Increment page counter.
            if len(data) < current_limit:  # Stop when fewer than requested are returned.
                break  # No more data expected.
            time.sleep(0.08)  # Gentle pacing for API safety.
        except requests.exceptions.RequestException as e:  # Catch HTTP/network issues.
            LOGGER.error(f"NETWORK ERROR during forward pagination: {e}")
            break  # Stop fetching.
        except Exception as e:  # Catch unexpected failures.
            LOGGER.error(f"UNEXPECTED EXTRACTION ERROR during forward pagination: {e}")
            break  # Stop fetching.
    return all_raw_data  # Return accumulated raw candles.

# ---------------------------------------------------------------------------
# Public: get_klines
# ---------------------------------------------------------------------------

def get_klines(
    symbol: str,
    interval: str = "1h",
    total_candles: int = 1000,
    use_cache: bool = True,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> pd.DataFrame:
    """Fetch the latest historical OHLCV data for a symbol."""  # Main project fetch function.

    # -------------------------------------------------------------------
    # Normalize and validate symbol
    # -------------------------------------------------------------------

    normalized = _normalize_symbol(symbol)  # Normalize input symbol.

    if not _validate_symbol(normalized):  # Validate symbol format.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return pd.DataFrame()  # Return empty DataFrame.

    # -------------------------------------------------------------------
    # Validate interval
    # -------------------------------------------------------------------

    if not _validate_interval(interval):  # Validate timeframe interval.
        LOGGER.error(f"Invalid interval: {interval}")
        return pd.DataFrame()  # Return empty DataFrame.

    # -------------------------------------------------------------------
    # Force integer conversion and minimum value
    # -------------------------------------------------------------------

    try:
        total_candles = int(total_candles)  # Convert to integer safely.
    except Exception:
        LOGGER.error(f"Invalid total_candles value: {total_candles}")
        return pd.DataFrame()  # Return empty DataFrame.

    total_candles = max(1, total_candles)  # Prevent zero or negative values.

    # -------------------------------------------------------------------
    # HARD SAFETY LIMIT
    # Prevent runaway requests freezing the server
    # -------------------------------------------------------------------

    MAX_ALLOWED_CANDLES = 50000  # Emergency server protection ceiling.

    if total_candles > MAX_ALLOWED_CANDLES:  # Clamp oversized requests.
        LOGGER.warning(
            f"Requested candles exceeded limit "
            f"({total_candles}). Clamping to {MAX_ALLOWED_CANDLES}."
        )
        total_candles = MAX_ALLOWED_CANDLES  # Force safe limit.

    # -------------------------------------------------------------------
    # Build cache key
    # -------------------------------------------------------------------

    cache_key = f"klines_{normalized}_{interval}_{total_candles}"  # Unique cache identifier.

    # -------------------------------------------------------------------
    # Attempt cache retrieval
    # -------------------------------------------------------------------

    if use_cache:  # Only use cache if enabled.
        try:
            cached = _load_cache(cache_key)  # Attempt cache load.
            if cached is not None and len(cached) >= total_candles:
                LOGGER.info(
                    f"Returning cached klines for "
                    f"{normalized} {interval}"
                )
                return cached.tail(total_candles)  # Return cached subset.
        except Exception as cache_error:
            LOGGER.warning(f"Cache read failed: {cache_error}")

    # -------------------------------------------------------------------
    # Log fetch operation
    # -------------------------------------------------------------------

    LOGGER.info(
        f"Fetching klines | "
        f"Symbol={normalized} | "
        f"Interval={interval} | "
        f"Candles={total_candles}"
    )

    # -------------------------------------------------------------------
    # Fetch raw candle data
    # -------------------------------------------------------------------

    raw_data = _fetch_klines_backward(
        normalized,
        interval,
        total_candles,
    )

    # -------------------------------------------------------------------
    # Validate API response
    # -------------------------------------------------------------------

    if not raw_data:
        LOGGER.warning("EMPTY DATA from _fetch_klines_backward")
        return pd.DataFrame()

    # -------------------------------------------------------------------
    # Build DataFrame
    # -------------------------------------------------------------------

    df = _build_ohlcv_dataframe(raw_data)

    # -------------------------------------------------------------------
    # Validate DataFrame
    # -------------------------------------------------------------------

    if df.empty:
        LOGGER.warning("Generated DataFrame is empty after processing.")
        return pd.DataFrame()

    # -------------------------------------------------------------------
    # Keep only requested number of candles
    # -------------------------------------------------------------------

    df = df.tail(total_candles)

    # -------------------------------------------------------------------
    # Save cache safely
    # -------------------------------------------------------------------

    if use_cache and not df.empty:
        try:
            _save_cache(
                cache_key,
                df,
                cache_ttl_seconds,
            )
        except Exception as cache_error:
            LOGGER.warning(f"Cache save failed: {cache_error}")

    # -------------------------------------------------------------------
    # Final logging
    # -------------------------------------------------------------------

    LOGGER.info(f"Klines fetch complete | Returned candles={len(df)}")

    # -------------------------------------------------------------------
    # Return final DataFrame
    # -------------------------------------------------------------------

    return df

# ---------------------------------------------------------------------------
# Public: get_klines_by_date
# ---------------------------------------------------------------------------

def get_klines_by_date(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    max_candles: int = 50000,
    use_cache: bool = True,
    cache_ttl_seconds: int = 5000,
) -> pd.DataFrame:
    """Fetch OHLCV candles for a specific date range."""  # Historical window fetch.
    normalized = _normalize_symbol(symbol)  # Normalize the symbol.
    if not _validate_symbol(normalized):  # Validate symbol format.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return pd.DataFrame()  # Return empty DataFrame.
    if not _validate_interval(interval):  # Validate interval.
        LOGGER.error(f"Invalid interval: {interval}")
        return pd.DataFrame()  # Return empty DataFrame.
    if start >= end:  # Validate range order.
        LOGGER.error("Start datetime must be before end datetime.")
        return pd.DataFrame()  # Return empty DataFrame.
    if start.tzinfo is None:  # Attach UTC to naive datetimes.
        start = start.replace(tzinfo=timezone.utc)  # Assume UTC for naive input.
    if end.tzinfo is None:  # Attach UTC to naive datetimes.
        end = end.replace(tzinfo=timezone.utc)  # Assume UTC for naive input.
    start_ms = int(start.timestamp() * 1000)  # Convert to milliseconds.
    end_ms = int(end.timestamp() * 1000)  # Convert to milliseconds.
    max_candles = max(1, int(max_candles))  # Enforce minimum count.
    cache_key = f"klines_date_{normalized}_{interval}_{start_ms}_{end_ms}_{max_candles}"  # Cache key.
    if use_cache:  # Check cache only when enabled.
        cached = _load_cache(cache_key)  # Try cache read.
        if cached is not None:  # Continue if cached data exists.
            df_slice = cached.loc[(cached.index >= start) & (cached.index <= end)]  # Slice requested range.
            if not df_slice.empty:  # Return only if range has data.
                return df_slice  # Return cached slice.
    raw_data = _fetch_klines_forward(normalized, interval, start_ms, end_ms, max_candles)  # Fetch range.
    if not raw_data:  # Stop if nothing was returned.
        return pd.DataFrame()  # Return empty DataFrame.
    df = _build_ohlcv_dataframe(raw_data)  # Build DataFrame.
    if df.empty:  # Check again after cleaning.
        return df  # Return empty DataFrame.
    df = df.loc[(df.index >= start) & (df.index <= end)]  # Keep only requested window.
    if use_cache and not df.empty:  # Save useful results.
        _save_cache(cache_key, df, cache_ttl_seconds)  # Persist date-range cache.
    return df  # Return cleaned data.

# ---------------------------------------------------------------------------
# Async public: get_price
# ---------------------------------------------------------------------------

async def async_get_price(symbol: str) -> float:
    """Async version of get_price."""  # Async price helper.
    if aiohttp is None:  # Check async support.
        raise ImportError("aiohttp is required for async_get_price")
    normalized = _normalize_symbol(symbol)  # Normalize symbol.
    if not _validate_symbol(normalized):  # Validate symbol.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return 0.0  # Return fallback.
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"  # Price endpoint.
    params = {"symbol": normalized}  # Query params.

    # FIX #1: Replaced infinite `while True` with bounded retry counter in async version too.
    rate_limit_attempts = 0

    async with aiohttp.ClientSession() as session:  # Create async session.
        while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded retry loop.
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    # FIX #3: Reduced async timeout from 5s -> 10s for consistency with sync version.
                    text = await resp.text()  # Read response text.
                    if resp.status == 418:  # Hard ban.
                        LOGGER.error("IP BANNED by Binance (HTTP 418) in async_get_price.")
                        return 0.0  # Stop immediately.
                    if resp.status == 429:  # Rate-limit response.
                        LOGGER.warning("Rate limit hit (429) in async_get_price. Sleeping 5s...")
                        await asyncio.sleep(5)  # Wait before retry.
                        rate_limit_attempts += 1
                        if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:
                            LOGGER.error(
                                f"async_get_price: exceeded {MAX_RATE_LIMIT_RETRIES} "
                                f"rate-limit retries for {symbol}. Giving up."
                            )
                            return 0.0  # Give up instead of looping forever.
                        continue  # Retry.
                    if resp.status != 200:  # Reject errors.
                        LOGGER.error(f"API ERROR [Status {resp.status}] in async_get_price: {text}")
                        return 0.0  # Return fallback.
                    data = json.loads(text)  # Parse JSON.
                    if not isinstance(data, dict) or "price" not in data:  # Validate format.
                        LOGGER.error(f"INVALID DATA FORMAT in async_get_price: {data}")
                        return 0.0  # Return fallback.
                    return float(data["price"])  # Return the price.
            except asyncio.TimeoutError:  # Catch timeout.
                LOGGER.error("Timeout in async_get_price")
                return 0.0  # Return fallback.
            except Exception as e:  # Catch unexpected errors.
                LOGGER.error(f"Unexpected error in async_get_price: {e}")
                return 0.0  # Return fallback.
    return 0.0  # Fallback if loop exits without returning.

# ---------------------------------------------------------------------------
# Async public: get_klines
# ---------------------------------------------------------------------------

async def async_get_klines(
    symbol: str,
    interval: str = "1h",
    total_candles: int = 1000,
) -> pd.DataFrame:
    """Async version of get_klines."""  # Async candle fetch.
    if aiohttp is None:  # Check optional dependency.
        raise ImportError("aiohttp is required for async_get_klines")
    normalized = _normalize_symbol(symbol)  # Normalize symbol.
    if not _validate_symbol(normalized):  # Validate symbol.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return pd.DataFrame()  # Return empty DataFrame.
    if not _validate_interval(interval):  # Validate interval.
        LOGGER.error(f"Invalid interval: {interval}")
        return pd.DataFrame()  # Return empty DataFrame.
    total_candles = max(1, int(total_candles))  # Enforce minimum count.
    url = f"{BINANCE_BASE_URL}/api/v3/klines"  # Candles endpoint.
    all_raw_data: List[List[Any]] = []  # Collect candles.
    remaining = total_candles  # Remaining candles to fetch.
    current_end_time: Optional[int] = None  # Backward page cursor.
    seen_timestamps = set()  # Avoid duplicates.

    # FIX #1/#2: Bounded rate-limit retries and page ceiling in async pagination.
    page_count = 0

    async with aiohttp.ClientSession() as session:  # Create async session.
        while remaining > 0:  # Keep fetching until satisfied.
            if page_count >= MAX_PAGINATION_PAGES:  # Hard page stop.
                LOGGER.warning(
                    f"async_get_klines: reached page limit ({MAX_PAGINATION_PAGES}). "
                    f"Stopping pagination early."
                )
                break

            current_limit = min(remaining, MAX_API_LIMIT)  # Respect hard limit.
            params: Dict[str, Any] = {  # Build params.
                "symbol": normalized,  # Normalized symbol.
                "interval": interval,  # Desired interval.
                "limit": current_limit,  # Number of candles to fetch.
            }
            if current_end_time is not None:  # Add paging cursor when available.
                params["endTime"] = current_end_time  # Page backward.

            rate_limit_attempts = 0  # Reset per-page retry counter.

            try:
                while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded per-page retry.
                    async with session.get(
                        url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        text = await resp.text()  # Read body.
                        if resp.status == 418:  # Hard ban.
                            LOGGER.error("IP BANNED by Binance (HTTP 418) in async_get_klines.")
                            return _build_ohlcv_dataframe(all_raw_data).tail(total_candles)
                        if resp.status == 429:  # Rate limit.
                            LOGGER.warning("Rate limit hit (429) in async_get_klines. Sleeping 5s...")
                            await asyncio.sleep(5)  # Sleep then retry.
                            rate_limit_attempts += 1
                            if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:
                                LOGGER.error(
                                    f"async_get_klines: exceeded rate-limit retries on page "
                                    f"{page_count}. Stopping."
                                )
                                return _build_ohlcv_dataframe(all_raw_data).tail(total_candles)
                            continue  # Retry same page.
                        if resp.status != 200:  # Abort on server error.
                            LOGGER.error(f"API ERROR [Status {resp.status}] in async_get_klines: {text}")
                            return _build_ohlcv_dataframe(all_raw_data).tail(total_candles)
                        data = json.loads(text)  # Parse JSON.
                        if not isinstance(data, list):  # Validate format.
                            LOGGER.error(f"INVALID DATA in async_get_klines: {data}")
                            return _build_ohlcv_dataframe(all_raw_data).tail(total_candles)
                        if len(data) == 0:  # Stop on empty page.
                            remaining = 0  # Signal outer loop to exit.
                            break
                        new_rows = 0  # Count new rows.
                        for row in data:  # Iterate rows.
                            ts = row[0]  # Read timestamp.
                            if ts not in seen_timestamps:  # Filter duplicates.
                                all_raw_data.append(row)  # Store row.
                                seen_timestamps.add(ts)  # Remember timestamp.
                                new_rows += 1  # Increment count.
                        remaining -= new_rows  # Reduce remaining count.
                        current_end_time = int(data[0][0]) - 1  # Move cursor back.
                        page_count += 1  # Increment page count.
                        if len(data) < current_limit:  # Stop if no more pages.
                            remaining = 0  # Signal outer loop to exit.
                        else:
                            await asyncio.sleep(0.08)  # Gentle pacing.
                        break  # Exit per-page retry loop on success.
            except asyncio.TimeoutError:  # Catch async timeout.
                LOGGER.error("Timeout in async_get_klines")
                break  # Stop.
            except Exception as e:  # Catch unexpected failures.
                LOGGER.error(f"Unexpected error in async_get_klines: {e}")
                break  # Stop.

    df = _build_ohlcv_dataframe(all_raw_data)  # Build DataFrame.
    return df.tail(total_candles)  # Return requested tail.

# ---------------------------------------------------------------------------
# Async public: get_klines_by_date
# ---------------------------------------------------------------------------

async def async_get_klines_by_date(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    max_candles: int = 50000,
) -> pd.DataFrame:
    """Async version of get_klines_by_date."""  # Async range fetch.
    if aiohttp is None:  # Check optional dependency.
        raise ImportError("aiohttp is required for async_get_klines_by_date")
    normalized = _normalize_symbol(symbol)  # Normalize symbol.
    if not _validate_symbol(normalized):  # Validate symbol.
        LOGGER.error(f"Invalid symbol format: {symbol}")
        return pd.DataFrame()  # Return empty DataFrame.
    if not _validate_interval(interval):  # Validate interval.
        LOGGER.error(f"Invalid interval: {interval}")
        return pd.DataFrame()  # Return empty DataFrame.
    if start >= end:  # Validate ordering.
        LOGGER.error("Start datetime must be before end datetime.")
        return pd.DataFrame()  # Return empty DataFrame.
    if start.tzinfo is None:  # Make start UTC-aware.
        start = start.replace(tzinfo=timezone.utc)  # Assume UTC.
    if end.tzinfo is None:  # Make end UTC-aware.
        end = end.replace(tzinfo=timezone.utc)  # Assume UTC.
    start_ms = int(start.timestamp() * 1000)  # Convert start to ms.
    end_ms = int(end.timestamp() * 1000)  # Convert end to ms.
    max_candles = max(1, int(max_candles))  # Enforce minimum count.
    url = f"{BINANCE_BASE_URL}/api/v3/klines"  # Candles endpoint.
    all_raw_data: List[List[Any]] = []  # Store rows.
    remaining = max_candles  # Remaining candles to fetch.
    current_start = start_ms  # Forward cursor.
    seen_timestamps = set()  # Deduplicate rows.

    # FIX #1/#2: Bounded rate-limit retries and page ceiling.
    page_count = 0

    async with aiohttp.ClientSession() as session:  # Create async session.
        while remaining > 0 and current_start <= end_ms:  # Continue while valid.
            if page_count >= MAX_PAGINATION_PAGES:  # Hard page stop.
                LOGGER.warning(
                    f"async_get_klines_by_date: reached page limit ({MAX_PAGINATION_PAGES}). "
                    f"Stopping pagination early."
                )
                break

            current_limit = min(remaining, MAX_API_LIMIT)  # Respect API ceiling.
            params: Dict[str, Any] = {  # Build params.
                "symbol": normalized,  # Normalized symbol.
                "interval": interval,  # Selected interval.
                "limit": current_limit,  # Batch size.
                "startTime": current_start,  # Paging start cursor.
                "endTime": end_ms,  # Range end.
            }

            rate_limit_attempts = 0  # Reset per-page retry counter.

            try:
                while rate_limit_attempts <= MAX_RATE_LIMIT_RETRIES:  # Bounded per-page retry.
                    async with session.get(
                        url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        text = await resp.text()  # Read body.
                        if resp.status == 418:  # Hard ban.
                            LOGGER.error(
                                "IP BANNED by Binance (HTTP 418) in async_get_klines_by_date."
                            )
                            df = _build_ohlcv_dataframe(all_raw_data)
                            return df.loc[(df.index >= start) & (df.index <= end)] if not df.empty else df
                        if resp.status == 429:  # Rate limit.
                            LOGGER.warning(
                                "Rate limit hit (429) in async_get_klines_by_date. Sleeping 5s..."
                            )
                            await asyncio.sleep(5)  # Wait.
                            rate_limit_attempts += 1
                            if rate_limit_attempts > MAX_RATE_LIMIT_RETRIES:
                                LOGGER.error(
                                    f"async_get_klines_by_date: exceeded rate-limit retries on "
                                    f"page {page_count}. Stopping."
                                )
                                df = _build_ohlcv_dataframe(all_raw_data)
                                return (
                                    df.loc[(df.index >= start) & (df.index <= end)]
                                    if not df.empty else df
                                )
                            continue  # Retry.
                        if resp.status != 200:  # Abort on server error.
                            LOGGER.error(
                                f"API ERROR [Status {resp.status}] in async_get_klines_by_date: {text}"
                            )
                            remaining = 0  # Signal outer loop to stop.
                            break
                        data = json.loads(text)  # Parse JSON.
                        if not isinstance(data, list):  # Validate format.
                            LOGGER.error(f"INVALID DATA in async_get_klines_by_date: {data}")
                            remaining = 0  # Signal outer loop to stop.
                            break
                        if len(data) == 0:  # Stop on empty page.
                            remaining = 0  # Signal outer loop to exit.
                            break
                        new_rows = 0  # Count new rows.
                        for row in data:  # Iterate candles.
                            ts = row[0]  # Read timestamp.
                            if ts not in seen_timestamps:  # Filter duplicates.
                                all_raw_data.append(row)  # Store candle.
                                seen_timestamps.add(ts)  # Remember timestamp.
                                new_rows += 1  # Increment count.
                        remaining -= new_rows  # Update remaining count.
                        current_start = int(data[-1][0]) + 1  # Move forward cursor.
                        page_count += 1  # Increment page count.
                        if len(data) < current_limit:  # Stop if response was short.
                            remaining = 0  # Signal outer loop to exit.
                        else:
                            await asyncio.sleep(0.08)  # Gentle pacing.
                        break  # Exit per-page retry loop on success.
            except asyncio.TimeoutError:  # Catch async timeout.
                LOGGER.error("Timeout in async_get_klines_by_date")
                break  # Stop.
            except Exception as e:  # Catch unexpected exceptions.
                LOGGER.error(f"Unexpected error in async_get_klines_by_date: {e}")
                break  # Stop.

    df = _build_ohlcv_dataframe(all_raw_data)  # Build DataFrame.
    if df.empty:  # Check if nothing survived cleaning.
        return df  # Return empty DataFrame.
    df = df.loc[(df.index >= start) & (df.index <= end)]  # Slice exact requested window.
    return df  # Return filtered DataFrame.

# ---------------------------------------------------------------------------
# Public aliases for compatibility
# ---------------------------------------------------------------------------

__all__ = [  # Export the main API.
    "get_price",  # Sync price fetch.
    "get_24hr_ticker",  # 24h stats.
    "get_server_time",  # Binance server time.
    "get_klines",  # Main historical candles fetcher.
    "get_klines_by_date",  # Date-range fetcher.
    "async_get_price",  # Async price fetch.
    "async_get_klines",  # Async candles fetcher.
    "async_get_klines_by_date",  # Async date-range fetcher.
]
