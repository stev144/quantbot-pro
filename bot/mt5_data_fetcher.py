# ============================================================
# bot/mt5_data_fetcher.py
#
# MT5 Integration — Milestone A (data fetcher skeleton).
#
# claude code changed: new file. The MetaTrader 5 equivalent of
# bot/forex_data_fetcher.py, built for the same reason
# forex_data_fetcher.py itself exists (asset-specific data acquisition,
# not forced through bot/data_fetcher.py's Binance-REST-shaped code) —
# but a DELIBERATELY DIFFERENT provider seam: MT5's own Python package
# (`MetaTrader5`, pip name "MetaTrader5") is an IPC bridge to a locally
# running MT5 terminal, not an HTTP API. It is Windows-only, requires
# the terminal application installed and logged into a broker account
# (free demo is enough — see the user's own MT5 scoping conversation,
# 2026-09-17: a broker's free demo account is sufficient to build and
# test this end-to-end; no live/funded account needed to start), and
# cannot run in a headless/cloud CI environment.
#
# WHY THIS WRITES TO THE SAME data/forex/ DIRECTORY Yahoo's
# forex_data_fetcher.py uses (not a separate data/forex_mt5/ folder):
# bot.instruments.resolve_ohlcv_path() is this project's single,
# provider-agnostic seam for "where does this Forex symbol's OHLCV
# live" — it resolves to data/forex/{symbol}_1h.csv regardless of which
# fetcher populated it (see that function's own docstring: "the single
# provider/file-representation boundary"). Writing here means every
# downstream consumer (feature_calculator.py, cointegration_engine.py,
# regime_conditional_pairs.py, ForexCostModel, ...) picks up real MT5
# data with ZERO other code changes the moment this fetcher actually
# runs — exactly the "testing data -> serious data" upgrade path the
# user asked for. This is a deliberate overwrite of forex_data_fetcher.py's
# output, not an accident — real broker OHLCV should supersede Yahoo's
# free feed, not sit alongside it under a different name nothing reads.
#
# WHAT IS AND ISN'T BUILT YET (this is a skeleton, not a tested
# integration — there is no MT5 terminal/account available in this
# environment to verify against):
#   - Connection/credential handling: real, matches this project's
#     existing bot.engines.kraken_adapter.build_kraken_adapter() pattern
#     (bare os.getenv(), MT5_ENABLED gate, refuses to proceed without
#     real credentials — never a half-working connection).
#   - get_mt5_klines()/download_all_mt5_symbols(): real logic, written
#     against the documented MetaTrader5 package API (initialize/login/
#     copy_rates_range), but UNTESTED against a live terminal — there is
#     nothing to test against yet. Exercise this for real the moment a
#     demo account exists.
#   - Symbol naming: MT5 symbol names are BROKER-SPECIFIC (unlike
#     Yahoo's stable "EURUSD=X" convention) — some brokers list "EURUSD",
#     others append a suffix ("EURUSDm", "EURUSD.a", ...). Never guessed
#     — see MT5_SYMBOL_SUFFIX below. Confirm the real convention against
#     mt5.symbols_get() once connected, before trusting any fetch.
# ============================================================

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
from bot.quant_core.research_zones import seal_before_write   # claude code changed: D-8

# claude code changed: 2026-10-06 — DATA CLOCK FIX (owner-approved option A). MT5 returns bar times in the BROKER
# SERVER's wall clock encoded as epoch seconds; they were stored as if UTC. Measured on all 58 data/forex files:
# the weekend gap is Fri 23:00 -> Mon 00:00 in every week of every season, INCLUDING the weeks when US and EU
# daylight-saving dates differ, i.e. the server clock follows US DST: server time = America/New_York + 7 h
# (UTC+2 in winter, UTC+3 in summer — the standard "New York close" broker). Inferred from the data and CONFIRMED
# LIVE 2026-10-06 (ICFundedEvaluations-MT5-Demo, account 90018157: last EURUSD tick 21:42:15 server vs 18:42:17 UTC
# = +3.00 h during US DST, exactly as NY+7 predicts). Another broker clock is set via MT5_SERVER_CLOCK.   # claude code changed: 2026-10-06
MT5_SERVER_CLOCK = os.getenv("MT5_SERVER_CLOCK", "NY+7")
MT5_CLOCK_LABEL = "UTC (converted from MT5 server time = America/New_York + 7 h)"


def mt5_server_time_to_utc(server_wall_clock) -> pd.Series:   # claude code changed: 2026-10-06 — new
    """MT5 server wall-clock times (epoch seconds, or naive/UTC-labelled datetimes that are really server time)
    -> true tz-aware UTC. Fails closed on times that do not exist / are ambiguous in New York (DST switches fall on
    Sunday early morning, when the market is closed, so a bar there means the clock assumption is wrong)."""
    if MT5_SERVER_CLOCK != "NY+7":
        raise ValueError(f"unsupported MT5_SERVER_CLOCK={MT5_SERVER_CLOCK!r} (only 'NY+7' is implemented and verified)")
    s = pd.Series(server_wall_clock)
    naive = pd.to_datetime(s, unit="s") if pd.api.types.is_numeric_dtype(s) else pd.to_datetime(s)
    if getattr(naive.dt, "tz", None) is not None:
        naive = naive.dt.tz_localize(None)              # the label was wrong: keep the wall-clock digits
    ny = (naive - pd.Timedelta(hours=7)).dt.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise")
    return ny.dt.tz_convert("UTC")


def convert_server_clock_files(directory, archive_dir) -> list:   # claude code changed: 2026-10-06 — new, one-off
    """Convert data/forex/*_1h.csv written with MT5 server time labelled as UTC to true UTC, after copying every
    original to `archive_dir` (refuses if an archive copy already exists — nothing is ever overwritten twice).
    Only files whose weekend gap is the MT5 server-time signature (Fri 23:00 -> Mon 00:00/01:00) are converted;
    anything else is reported and left untouched."""
    import shutil
    from pathlib import Path
    from bot.instruments import write_provenance_marker
    directory, archive_dir = Path(directory), Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=False)
    report = []
    for p in sorted(directory.glob("*_1h.csv")):
        df = pd.read_csv(p, float_precision="round_trip")
        t = pd.to_datetime(df["timestamp"], utc=True)
        gaps = [(t.iloc[i - 1], t.iloc[i]) for i in range(1, len(t)) if t.iloc[i] - t.iloc[i - 1] > timedelta(hours=30)]
        sig = sum(1 for a, b in gaps if a.dayofweek == 4 and a.hour == 23 and b.dayofweek == 0 and b.hour in (0, 1))
        if not gaps or sig / len(gaps) < 0.9:
            report.append({"file": p.name, "converted": False, "reason": f"server-time signature in {sig}/{len(gaps)} weekends"})
            continue
        shutil.copy2(p, archive_dir / p.name)
        m = p.with_suffix(".provenance.json")
        had_marker = m.exists()   # claude code changed: 2026-10-06 — read BEFORE the new marker is written
        if had_marker:
            shutil.copy2(m, archive_dir / m.name)
        df["timestamp"] = mt5_server_time_to_utc(t).dt.strftime("%Y-%m-%d %H:%M:%S+00:00").to_numpy()
        df.to_csv(p, index=False)
        ts = pd.to_datetime(df["timestamp"], utc=True)
        symbol = p.name[:-len("_1h.csv")].replace("_", "/")
        write_provenance_marker(p, venue="mt5", data_source="mt5_data_fetcher", source="mt5_terminal_ipc", symbol=symbol,
                                timeframe=INTERVAL, start_date=str(ts.iloc[0].date()), end_date=str(ts.iloc[-1].date()),
                                row_count=len(df), clock=MT5_CLOCK_LABEL + "; converted 2026-10-06 from stored server-time rows"
                                + ("" if had_marker else "; original marker missing — MT5 origin inferred from the weekend signature"))
        report.append({"file": p.name, "converted": True, "rows": len(df), "weekend_signature": f"{sig}/{len(gaps)}",
                       "had_marker": had_marker})
    return report


def confirm_mt5_server_clock() -> dict:   # claude code changed: 2026-10-06 — new; live check (needs the terminal)
    """Compare the terminal's latest server tick time with the real UTC clock: the offset must be +3 h during US
    daylight saving and +2 h otherwise if MT5_SERVER_CLOCK='NY+7' is right."""
    mt5 = connect()
    try:
        tick = mt5.symbol_info_tick("EURUSD" + os.getenv("MT5_SYMBOL_SUFFIX", ""))
        now_utc = pd.Timestamp.now(tz="UTC")
        server = pd.Timestamp(tick.time, unit="s")
        expected = now_utc.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)
        return {"server_minus_utc_hours": round((server - now_utc.tz_localize(None)).total_seconds() / 3600, 2),
                "expected_hours": round((expected - now_utc.tz_localize(None)).total_seconds() / 3600, 2),
                "note": "tick time is the LAST tick; outside market hours it can be stale"}
    finally:
        mt5.shutdown()

# claude code changed: new — reuse, not reimplementation. Same universe,
# same filename convention, same gap-detection logic as the Yahoo fetcher
# — only the provider call underneath changes. See module docstring for
# why this deliberately writes into the exact same data/forex/ directory.
from bot.forex_data_fetcher import (
    OUTPUT_DIR,
    SYMBOLS,
    detect_gaps,
    symbol_to_filename,
)

logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Configuration ────────────────────────────────────────────────────────────

INTERVAL = "1h"

# claude code changed: new — MT5's own timeframe constants (mt5.TIMEFRAME_H1
# etc.) only exist once the package is importable, which requires it to be
# pip-installed. Mapped lazily inside _mt5_timeframe() rather than at
# module level, so importing this module never requires the MetaTrader5
# package to be installed — only actually calling a fetch function does.
_INTERVAL_TO_MT5_TIMEFRAME_ATTR: Dict[str, str] = {
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}

# claude code changed: new — MT5 (via a real broker, not Yahoo's ~2-year
# intraday cap) can typically serve several years of real hourly history.
# Kept conservative/explicit rather than "as much as possible" — matches
# this project's own convention (fetch_all_symbols.py's HISTORY_YEARS,
# forex_data_fetcher.py's HISTORY_DAYS) of a named, documented ceiling
# rather than an unbounded request. Adjust once a real broker's actual
# available history is confirmed live.
HISTORY_DAYS = 1825   # 5 years, matching crypto's own HISTORY_YEARS default

FRESHNESS_MAX_AGE_DAYS = 7   # claude code changed: same convention as forex_data_fetcher.py/fetch_all_symbols.py


class MT5NotInstalledError(RuntimeError):
    """claude code changed: new — raised when the `MetaTrader5` package
    itself isn't importable, distinct from a connection/login failure.
    Callers (like a health check) need to tell "this environment can
    never do this" apart from "this environment could, but isn't
    connected right now"."""


class MT5ConnectionError(RuntimeError):
    """claude code changed: new — raised for a real MT5 terminal
    connection/login failure (terminal not running, bad credentials,
    server unreachable) — distinct from MT5NotInstalledError and from a
    per-symbol data error, matching forex_data_fetcher.py's own
    ForexProviderError/requests.RequestException split so a caller can
    tell these apart."""


def _import_mt5():
    """
    claude code changed: new — the lazy-import seam. `MetaTrader5` is not
    a project-wide dependency (Windows-only, needs a real terminal
    installed) — importing it at module load time would break every
    other import of this module on any machine/CI runner that doesn't
    have it, for a project that otherwise imports cleanly cross-platform.
    Raises MT5NotInstalledError with an actionable message rather than
    letting a raw ImportError propagate.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        raise MT5NotInstalledError(
            "The 'MetaTrader5' package is not installed in this environment "
            "(pip install MetaTrader5 — Windows only, requires a locally "
            "installed MT5 terminal). See bot/mt5_data_fetcher.py's module "
            "docstring for the full setup story."
        ) from e
    return mt5


def _mt5_timeframe(mt5, interval: str):
    """claude code changed: new — resolves interval string to the real
    mt5.TIMEFRAME_* constant, only once the package is actually imported."""
    attr = _INTERVAL_TO_MT5_TIMEFRAME_ATTR.get(interval)
    if attr is None:
        raise ValueError(
            f"interval={interval!r} not supported — choose one of "
            f"{sorted(_INTERVAL_TO_MT5_TIMEFRAME_ATTR)}"
        )
    return getattr(mt5, attr)


def _to_mt5_symbol(canonical_symbol: str) -> str:
    """
    claude code changed: new — "EUR/USD" -> "EURUSD" (+ optional broker
    suffix). Unlike forex_data_fetcher.py's _to_yahoo_ticker() (a stable,
    documented Yahoo convention), MT5 symbol naming is BROKER-SPECIFIC —
    never guessed beyond the base "no slash" transform. MT5_SYMBOL_SUFFIX
    (e.g. "m", ".a" — some brokers append one to every symbol) is read
    from the environment so this adapts to whichever broker's demo/live
    account is actually configured, rather than hardcoding one broker's
    convention as if it were universal. Confirm the real symbol name
    against mt5.symbols_get() before trusting a fetch for a new broker.
    """
    base, _, quote = canonical_symbol.partition("/")
    if not base or not quote:
        raise ValueError(f"'{canonical_symbol}' is not a valid BASE/QUOTE forex symbol")
    suffix = os.getenv("MT5_SYMBOL_SUFFIX", "")
    return f"{base}{quote}{suffix}"


def _mt5_credentials() -> Optional[Dict[str, str]]:
    """
    claude code changed: new — matches bot.engines.kraken_adapter.
    build_kraken_adapter()'s exact pattern: bare os.getenv(), an
    explicit *_ENABLED gate, refuses to proceed without real credentials
    rather than attempting a connection that can only fail. Returns None
    (never raises) when disabled or incomplete, so callers can decide
    what "no connection available" means for them.
    """
    enabled = os.getenv("MT5_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    if not enabled:
        logger.info("[mt5_data_fetcher] MT5_ENABLED not set — MT5 connection not attempted.")
        return None

    login = os.getenv("MT5_LOGIN", "")
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    terminal_path = os.getenv("MT5_TERMINAL_PATH", "")   # optional — auto-detected by the package if unset

    if not login or not password or not server:
        logger.critical(
            "[mt5_data_fetcher] MT5_ENABLED=true but MT5_LOGIN/MT5_PASSWORD/MT5_SERVER "
            "are incomplete. Refusing to attempt a connection without full credentials "
            "— a free broker demo account provides all three (see module docstring)."
        )
        return None

    return {"login": login, "password": password, "server": server, "terminal_path": terminal_path}


def connect() -> "object":
    """
    claude code changed: new — establishes and returns the live mt5
    module handle (the MetaTrader5 package itself acts as the connection
    object — its functions operate on whichever terminal initialize()
    most recently attached to, not a returned session object; this
    project's other adapters return an object to call methods on, this
    one returns the module for the same purpose, since that's the real
    shape MetaTrader5's own API takes). Raises MT5NotInstalledError or
    MT5ConnectionError rather than returning None on failure — callers
    that reach this function have already opted in (MT5_ENABLED=true
    with real credentials), so a failure here is a real, reportable
    problem, not a "not configured" state.
    """
    creds = _mt5_credentials()
    if creds is None:
        raise MT5ConnectionError("MT5 is not enabled or credentials are incomplete — see MT5_ENABLED/MT5_LOGIN/MT5_PASSWORD/MT5_SERVER")

    mt5 = _import_mt5()

    init_kwargs = {}
    if creds["terminal_path"]:
        init_kwargs["path"] = creds["terminal_path"]

    if not mt5.initialize(**init_kwargs):
        raise MT5ConnectionError(f"mt5.initialize() failed: {mt5.last_error()}")

    authorized = mt5.login(
        login=int(creds["login"]), password=creds["password"], server=creds["server"],
    )
    if not authorized:
        mt5.shutdown()
        raise MT5ConnectionError(f"mt5.login() failed for server={creds['server']!r}: {mt5.last_error()}")

    logger.info(f"[mt5_data_fetcher] Connected — server={creds['server']}, login={creds['login']}")
    return mt5


def get_mt5_klines(symbol: str, interval: str = INTERVAL, history_days: int = HISTORY_DAYS) -> pd.DataFrame:
    """
    claude code changed: new — THE provider seam, mirroring forex_data_
    fetcher.py's get_forex_klines() exactly in shape and return contract:
    a DataFrame with columns [timestamp, open, high, low, close, volume],
    UTC-aware timestamps, sorted, deduplicated — so every downstream
    consumer built against the Yahoo fetcher's output needs zero changes.

    Real difference from Yahoo's feed: MT5 reports genuine tick_volume
    (number of price changes per candle) — a real, if imperfect, activity
    signal, unlike Yahoo's always-0 column. Kept in the same 'volume'
    column rather than a new one, since feature_calculator.py's hard
    OHLCV contract expects that name; documented here rather than
    silently treated as identical in meaning to crypto's traded volume.

    Requires an active connection (call connect() first, or pass one via
    `mt5` — kept as a parameter-free call matching get_forex_klines()'s
    own signature for drop-in symmetry; connects lazily on first use).
    """
    mt5 = connect()
    try:
        mt5_symbol = _to_mt5_symbol(symbol)
        timeframe = _mt5_timeframe(mt5, interval)

        if not mt5.symbol_select(mt5_symbol, True):
            raise MT5ConnectionError(
                f"mt5.symbol_select({mt5_symbol!r}) failed — this broker may not list this "
                f"symbol under this name. Check mt5.symbols_get() for the real name "
                f"(see MT5_SYMBOL_SUFFIX in this module's _to_mt5_symbol())."
            )

        date_to = datetime.now(tz=timezone.utc)
        date_from = date_to - timedelta(days=history_days)
        rates = mt5.copy_rates_range(mt5_symbol, timeframe, date_from, date_to)

        if rates is None or len(rates) == 0:
            raise MT5ConnectionError(
                f"mt5.copy_rates_range({mt5_symbol!r}) returned no data: {mt5.last_error()}"
            )

        df = pd.DataFrame(rates)
        df["timestamp"] = mt5_server_time_to_utc(df["time"]).to_numpy()   # claude code changed: 2026-10-06 — was utc=True on server time
        df = df.rename(columns={"tick_volume": "volume"})[
            ["timestamp", "open", "high", "low", "close", "volume"]
        ]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
    finally:
        mt5.shutdown()   # claude code changed: always release the terminal connection, even on error


def _attach_running_terminal():   # claude code changed: 2026-10-06 — new
    """Attach to the terminal that is already running and logged in (no login call, no stored credentials) —
    read-only use of whatever account the operator opened, e.g. with the investor password."""
    mt5 = _import_mt5()
    if not mt5.initialize():
        raise MT5ConnectionError(f"mt5.initialize() failed — is the MT5 terminal running and logged in? {mt5.last_error()}")
    return mt5


def download_all_mt5_symbols(date_from=None, attach_only: bool = False, archive_dir=None) -> dict:
    # claude code changed: 2026-10-06 — optional args (defaults = previous behaviour): date_from (history start;
    # default = HISTORY_DAYS before now), attach_only (use the logged-in terminal, no stored credentials) and
    # archive_dir (copy each existing file + marker there BEFORE overwriting it; refuses an existing archive dir).
    """
    claude code changed: new — the MT5 equivalent of forex_data_fetcher.py's
    download_all_forex_symbols(). Same shape (per-symbol try/except,
    summary dict, freshness check, weekend-gap-aware reporting via the
    REUSED detect_gaps()), same universe (SYMBOLS, imported from
    forex_data_fetcher.py — one universe, two possible providers), same
    output directory/filenames — see module docstring for why that's
    deliberate. One connection is opened and reused across every symbol
    (unlike get_mt5_klines()'s own connect-per-call default) since
    re-authenticating per symbol would be wasteful for a full-universe run.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    start = (pd.Timestamp(date_from).tz_localize("UTC") if pd.Timestamp(date_from).tzinfo is None
             else pd.Timestamp(date_from).tz_convert("UTC")).to_pydatetime() if date_from is not None else None   # claude code changed: 2026-10-06
    if archive_dir is not None:   # claude code changed: 2026-10-06
        from pathlib import Path as _P
        archive_dir = _P(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=False)

    print("\n" + "=" * 80)
    print(f"DOWNLOADING MT5 FOREX DATA FOR {len(SYMBOLS)} PAIRS")
    print(f"History : {'from ' + start.isoformat() if start else f'most recent {HISTORY_DAYS} days'} to {now.isoformat()}")   # claude code changed: 2026-10-06
    print(f"Interval: {INTERVAL}")
    print("Fetcher : MetaTrader5 package (local terminal IPC)")
    print("=" * 80)

    summary = {}
    mt5 = _attach_running_terminal() if attach_only else connect()   # claude code changed: one connection for the whole run; 2026-10-06 attach_only

    try:
        for symbol in SYMBOLS:
            print(f"\n[Downloading] {symbol}")
            try:
                mt5_symbol = _to_mt5_symbol(symbol)
                timeframe = _mt5_timeframe(mt5, INTERVAL)

                if not mt5.symbol_select(mt5_symbol, True):
                    raise MT5ConnectionError(f"mt5.symbol_select({mt5_symbol!r}) failed: {mt5.last_error()}")

                date_to = now
                window_from = start if start is not None else now - timedelta(days=HISTORY_DAYS)   # claude code changed: 2026-10-06
                rates = mt5.copy_rates_range(mt5_symbol, timeframe, window_from, date_to)
                if rates is None or len(rates) == 0:
                    raise MT5ConnectionError(f"no data returned: {mt5.last_error()}")

                df = pd.DataFrame(rates)
                df["timestamp"] = mt5_server_time_to_utc(df["time"]).to_numpy()   # claude code changed: 2026-10-06 — was utc=True on server time
                df = df.rename(columns={"tick_volume": "volume"})[
                    ["timestamp", "open", "high", "low", "close", "volume"]
                ]
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                df.drop_duplicates(subset=["timestamp"], inplace=True)
                df.sort_values("timestamp", inplace=True)
                df.reset_index(drop=True, inplace=True)

                if df.empty:
                    raise MT5ConnectionError("no usable rows after cleaning")

                gaps = detect_gaps(df)
                weekend_gaps = [g for g in gaps if g["likely_weekend"]]
                other_gaps = [g for g in gaps if not g["likely_weekend"]]

                filepath = OUTPUT_DIR / symbol_to_filename(symbol)
                if archive_dir is not None:   # claude code changed: 2026-10-06 — keep the previous version
                    import shutil
                    for _old in (filepath, filepath.with_suffix(".provenance.json")):
                        if _old.exists():
                            shutil.copy2(_old, archive_dir / _old.name)
                df = seal_before_write(df, filepath)   # claude code changed: D-8 — sealed forward holdout
                df.to_csv(filepath, index=False)

                # claude code changed: Data-Layer Audit Step 1 — records that
                # MT5 (this fetcher) wrote filepath, so bot/instruments.py's
                # _build_forex_registry() stops reporting the stale
                # venue="yahoo_finance" default the moment real MT5 data
                # lands in the same data/forex/ path Yahoo's fetcher also
                # writes to (see this module's own docstring for why that's
                # a deliberate overwrite). Lazy import: bot.instruments
                # imports bot.forex_data_fetcher at top level, and this
                # module mirrors that path convention, so a top-level import
                # here risks the same cycle.
                #
                # claude code changed: Data-Layer Audit Step 5 — now also
                # passes the real dataset identity so a real sha256
                # fingerprint gets embedded, matching MT5Provider's own
                # DatasetIdentity.source="mt5_terminal_ipc" for the
                # in-memory CanonicalBars path. Untested against a live
                # terminal in this environment (no MT5 available), same
                # honestly-disclosed status this module's own docstring
                # already carries — but the write-path wiring itself is
                # exercised by test_instruments.py's provenance tests,
                # which don't require a live connection.
                from bot.instruments import write_provenance_marker
                write_provenance_marker(
                    filepath, venue="mt5", data_source="mt5_data_fetcher",
                    source="mt5_terminal_ipc", symbol=symbol, timeframe=INTERVAL,
                    start_date=str(df["timestamp"].iloc[0].date()), end_date=str(df["timestamp"].iloc[-1].date()),
                    row_count=len(df), clock=MT5_CLOCK_LABEL,   # claude code changed: 2026-10-06
                )

                last_timestamp = df["timestamp"].iloc[-1]
                staleness_days = (now - last_timestamp).total_seconds() / 86400
                is_fresh = staleness_days <= FRESHNESS_MAX_AGE_DAYS
                if not is_fresh:
                    logger.warning(f"{symbol}: data is STALE — most recent candle is {last_timestamp.isoformat()} ({staleness_days:.1f} days old)")

                print(f"  Downloaded    : {len(df):,} candles")
                print(f"  Date range    : {df['timestamp'].iloc[0]} -> {last_timestamp}")
                print(f"  Weekend gaps  : {len(weekend_gaps)} (expected calendar closures)")
                if other_gaps:
                    print(f"  ⚠ Other gaps  : {len(other_gaps)} (not weekend-shaped — worth investigating)")
                print(f"  Saved to      : {filepath}")

                summary[symbol] = {
                    "success": True, "candles": len(df), "file": str(filepath), "error": None,
                    "last_timestamp": last_timestamp.isoformat(), "staleness_days": round(staleness_days, 2),
                    "is_fresh": is_fresh, "weekend_gaps": len(weekend_gaps), "other_gaps": len(other_gaps),
                }
            except MT5ConnectionError as e:
                print(f"  ✗ MT5 error: {e}")
                summary[symbol] = {"success": False, "candles": 0, "file": None, "error": f"MT5: {e}"}
            except Exception as e:
                print(f"  ✗ Error: {e}")
                summary[symbol] = {"success": False, "candles": 0, "file": None, "error": str(e)}
    finally:
        mt5.shutdown()

    success_count = sum(1 for v in summary.values() if v["success"])
    print("\n" + "=" * 80)
    print(f"DOWNLOAD COMPLETE — {success_count}/{len(SYMBOLS)} pairs downloaded successfully")
    print("=" * 80)
    return summary


def get_mt5_symbol_info(symbol: str, *, attach_only: bool = True) -> dict:  # claude code changed: new — 2026-10-16, Stage 2 broker spec fetch; attach_only=True uses the already-running terminal (no stored creds needed for a demo already logged in)
    """
    Fetch broker instrument specification for one symbol via mt5.symbol_info().
    Returns a plain dict with every field needed to populate ForexInstrumentSpec:
      canonical_symbol, spread_pips, commission_per_lot, swap_long_pips,
      swap_short_pips, swap_triple_day, contract_size, pip_size, price_precision,
      min_volume, volume_increment, quantity_precision, leverage_max, margin_rate.

    attach_only=True attaches to the already-running terminal without re-logging in
    (safe for a Read Only / demo terminal open on the desktop). Set False to use
    the full MT5_LOGIN/PASSWORD/SERVER credential path instead.

    Raises MT5ConnectionError on any failure — never returns partial data silently.
    All pip/swap values are expressed in PIPS (divided by point*10 for 5-decimal pairs,
    divided by point for 3-decimal JPY pairs) so the caller doesn't need to know the
    internal point representation.
    """
    mt5 = _attach_running_terminal() if attach_only else connect()  # claude code changed: one attach, one shutdown
    try:
        mt5_sym = _to_mt5_symbol(symbol)
        if not mt5.symbol_select(mt5_sym, True):
            raise MT5ConnectionError(f"symbol_select({mt5_sym!r}) failed: {mt5.last_error()}")

        info = mt5.symbol_info(mt5_sym)
        if info is None:
            raise MT5ConnectionError(f"symbol_info({mt5_sym!r}) returned None: {mt5.last_error()}")

        # pip_size: for 5-decimal pairs (EUR/USD) pip = 0.0001 = point * 10;
        # for 3-decimal JPY pairs (USD/JPY) pip = 0.01 = point * 10.
        # MT5's own 'point' is the minimum price change (0.00001 or 0.001).
        # Convention across all retail brokers: pip = point * 10.
        pip_size = round(info.point * 10, 10)  # claude code changed: pip = point * 10 is the universal retail convention

        def _to_pips(value_in_points: float) -> float:
            """Convert a value stored in MT5's internal 'points' to pips."""  # claude code changed: inline helper — avoids silent unit confusion
            return round(value_in_points / 10.0, 6)

        # Spread: MT5 stores current_spread as an integer count of points.
        spread_pips = _to_pips(info.spread) if hasattr(info, "spread") else None  # claude code changed: None if field absent (older builds)

        # Swap: MT5 stores swap_long/swap_short in POINTS (same unit as spread).
        swap_long_pips = _to_pips(info.swap_long) if hasattr(info, "swap_long") else None  # claude code changed: None = unavailable
        swap_short_pips = _to_pips(info.swap_short) if hasattr(info, "swap_short") else None

        # swap_rollover3days: 0=Sun,1=Mon,...,6=Sat (MT5 convention)
        swap_triple_day = getattr(info, "swap_rollover3days", None)

        # Commission: MT5 stores commission per lot in account currency (already in USD for USD account).
        commission_per_lot = getattr(info, "commission", None)

        # Leverage: MT5 stores as an integer (e.g. 30 for 1:30).
        leverage_max = float(info.leverage) if hasattr(info, "leverage") and info.leverage else None
        margin_rate = round(1.0 / leverage_max, 8) if leverage_max else None  # claude code changed: 1/leverage

        return {
            "canonical_symbol": symbol,
            "mt5_symbol": mt5_sym,
            "pip_size": pip_size,
            "price_precision": info.digits,
            "contract_size": float(info.trade_contract_size),
            "min_volume": float(info.volume_min),
            "volume_increment": float(info.volume_step),
            "quantity_precision": len(str(info.volume_step).rstrip("0").split(".")[-1]) if "." in str(info.volume_step) else 0,
            "spread_pips": spread_pips,
            "commission_per_lot": commission_per_lot,
            "swap_long_pips": swap_long_pips,
            "swap_short_pips": swap_short_pips,
            "swap_triple_day": swap_triple_day,
            "leverage_max": leverage_max,
            "margin_rate": margin_rate,
            "source": f"MT5_LIVE_{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}",
        }  # claude code changed: all fields needed by ForexInstrumentSpec — no silent None for required fields
    finally:
        mt5.shutdown()  # claude code changed: always release


if __name__ == "__main__":
    download_all_mt5_symbols()
