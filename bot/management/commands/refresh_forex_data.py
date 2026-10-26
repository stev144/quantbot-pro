# bot/management/commands/refresh_forex_data.py  # claude code changed: new — hourly MT5 forex data refresh for the scanner
"""
Incremental MT5 data refresh for the Forex Market Scanner.

Reads the last timestamp from each data/forex/*_1h.csv, fetches only the
new candles since then from the running MT5 terminal, appends them, and
clears the scanner cache so the next page load picks up fresh data.

Designed to be called by a Windows Task Scheduler job every hour:
  python manage.py refresh_forex_data --attach

Usage:
  python manage.py refresh_forex_data            # uses MT5_LOGIN/PASSWORD/SERVER env vars
  python manage.py refresh_forex_data --attach   # attach to already-running MT5 terminal
  python manage.py refresh_forex_data --pair GBP/JPY --attach   # single pair
  python manage.py refresh_forex_data --full --attach            # full re-download (slow)

The --attach flag is the normal production path — the MT5 terminal is already open
and logged in, so no credential env vars are needed.

Cache key cleared: forex_scanner_v1 (same key as bot/views/forex_scanner.py).
"""
import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from django.core.cache import cache
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_ROOT       = Path(__file__).resolve().parents[3]
_FOREX_DIR  = _ROOT / "data" / "forex"
_OVERLAP_H  = 3          # fetch this many extra hours before last_ts to catch partial candles
_CACHE_KEY  = "forex_scanner_v1"


class Command(BaseCommand):
    help = "Incrementally refresh data/forex/*_1h.csv from MT5 and clear the scanner cache"

    def add_arguments(self, parser):
        parser.add_argument(
            "--attach", action="store_true",
            help="Attach to the already-running MT5 terminal (no credential env vars needed)",
        )
        parser.add_argument(
            "--full", action="store_true",
            help="Full re-download (slow — fetches HISTORY_DAYS of history for every pair)",
        )
        parser.add_argument(
            "--pair", type=str, default=None,
            help="Refresh only this pair, e.g. --pair GBP/JPY",
        )

    def handle(self, *args, **options):  # claude code changed: new — main entrypoint
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")

        attach  = options["attach"]
        full    = options["full"]
        pair    = options.get("pair")

        # ── import MT5 infrastructure (lazy — Windows-only dependency) ──────────
        try:
            from bot.mt5_data_fetcher import (
                MT5ConnectionError,
                MT5NotInstalledError,
                _attach_running_terminal,
                _mt5_timeframe,
                _to_mt5_symbol,
                connect,
                mt5_server_time_to_utc,
                MT5_CLOCK_LABEL,   # claude code changed: 2026-10-26 — the configured clock, not a hard-coded NY+7 label
                HISTORY_DAYS,
                symbol_to_filename,
            )
            from bot.forex_data_fetcher import SYMBOLS
            from bot.instruments import write_provenance_marker
        except MT5NotInstalledError as exc:
            self.stderr.write(self.style.ERROR(f"MT5 not installed: {exc}"))
            return
        except ImportError as exc:
            self.stderr.write(self.style.ERROR(f"Import error: {exc}"))
            return

        # ── decide which pairs to refresh ───────────────────────────────────────
        if pair:
            pairs = [pair]
        else:
            pairs = SYMBOLS

        # ── connect ─────────────────────────────────────────────────────────────
        self.stdout.write(f"[refresh_forex_data] Connecting to MT5 ({'attach' if attach else 'credentials'})...")
        try:
            mt5 = _attach_running_terminal() if attach else connect()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"MT5 connection failed: {exc}"))
            return

        now         = datetime.now(tz=timezone.utc)
        _account    = mt5.account_info()   # claude code changed: 2026-10-26 — record which broker server the appended rows came from
        broker      = _account.server if _account is not None else "unknown MT5 server"
        ok_count    = 0
        skip_count  = 0
        err_count   = 0
        new_rows    = 0

        try:
            for symbol in pairs:
                filepath = _FOREX_DIR / symbol_to_filename(symbol)
                mt5_symbol = _to_mt5_symbol(symbol)

                try:
                    timeframe = _mt5_timeframe(mt5, "1h")

                    # ── incremental: determine fetch window ──────────────────────
                    if not full and filepath.exists():
                        existing = pd.read_csv(filepath, usecols=["timestamp"],
                                               parse_dates=["timestamp"])
                        if not existing.empty:
                            last_ts = pd.to_datetime(
                                existing["timestamp"].iloc[-1], utc=True
                            )
                            # fetch from (last_ts - overlap) to now
                            fetch_from = (last_ts - timedelta(hours=_OVERLAP_H)).to_pydatetime()
                        else:
                            fetch_from = now - timedelta(days=HISTORY_DAYS)
                    else:
                        fetch_from = now - timedelta(days=HISTORY_DAYS)

                    # ── fetch from MT5 ──────────────────────────────────────────
                    if not mt5.symbol_select(mt5_symbol, True):
                        raise MT5ConnectionError(
                            f"symbol_select({mt5_symbol!r}) failed: {mt5.last_error()}"
                        )

                    rates = mt5.copy_rates_range(mt5_symbol, timeframe, fetch_from, now)
                    if rates is None or len(rates) == 0:
                        # No new candles — pair is up to date
                        skip_count += 1
                        self.stdout.write(f"  {symbol:12s}  up to date")
                        continue

                    new_df = pd.DataFrame(rates)
                    new_df["timestamp"] = mt5_server_time_to_utc(new_df["time"]).to_numpy()
                    new_df = new_df.rename(columns={"tick_volume": "volume"})[
                        ["timestamp", "open", "high", "low", "close", "volume"]
                    ]
                    for col in ["open", "high", "low", "close", "volume"]:
                        new_df[col] = pd.to_numeric(new_df[col], errors="coerce")
                    new_df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                    # claude code changed: 2026-10-26 — drop the bar that is still forming (opened less than an hour
                    # ago): saved now it would hold only part of the hour (a run at hh:00:01 saw one second of it).
                    new_df = new_df[pd.to_datetime(new_df["timestamp"], utc=True) + timedelta(hours=1) <= now]

                    # ── merge with existing — always, even on --full  # claude code changed: bug fix: --full controls fetch window, not whether to merge; replacing instead of merging lost pre-broker-history-limit rows
                    existing_full = None
                    if filepath.exists():
                        existing_full = pd.read_csv(filepath, parse_dates=["timestamp"])
                        combined = pd.concat([existing_full, new_df], ignore_index=True)
                    else:
                        combined = new_df

                    combined.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)   # claude code changed: 2026-10-26 — was keep="first": a re-fetched overlap bar now replaces the older (possibly partial) copy
                    combined.sort_values("timestamp", inplace=True)
                    combined.reset_index(drop=True, inplace=True)

                    added = len(combined) - (len(existing_full) if existing_full is not None else 0)
                    # claude code changed: 2026-10-26 — a replaced overlap bar adds no row but still has to be written
                    _cols = ["open", "high", "low", "close"]
                    revised = (existing_full is not None and added <= 0 and not combined[_cols].reset_index(drop=True).equals(
                        existing_full.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")[_cols].reset_index(drop=True)))
                    if added <= 0 and not full and not revised:
                        skip_count += 1
                        self.stdout.write(f"  {symbol:12s}  no new candles")
                        continue

                    # ── write ───────────────────────────────────────────────────
                    _FOREX_DIR.mkdir(parents=True, exist_ok=True)
                    combined.to_csv(filepath, index=False)

                    write_provenance_marker(
                        filepath, venue="mt5", data_source="refresh_forex_data",
                        source="mt5_terminal_ipc", symbol=symbol, timeframe="1h",
                        start_date=str(combined["timestamp"].iloc[0])[:10],
                        end_date=str(combined["timestamp"].iloc[-1])[:10],
                        row_count=len(combined),
                        clock=f"{MT5_CLOCK_LABEL}; rows appended on {now:%Y-%m-%d %H:%M} UTC came from MT5 server {broker}",   # claude code changed: 2026-10-26 — was a hard-coded NY+7 label
                    )

                    new_rows += max(added, 0)
                    ok_count += 1
                    self.stdout.write(
                        f"  {symbol:12s}  +{added} candles  "
                        f"(total {len(combined):,}  last={str(combined['timestamp'].iloc[-1])[:16]})"
                    )

                except Exception as exc:
                    err_count += 1
                    logger.warning("refresh_forex_data: %s failed: %s", symbol, exc)
                    self.stdout.write(self.style.WARNING(f"  {symbol:12s}  ERROR: {exc}"))

        finally:
            mt5.shutdown()

        # ── clear scanner cache so next page load re-reads the fresh CSVs ───────
        cache.delete(_CACHE_KEY)  # claude code changed: force scanner to re-compute
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {ok_count} updated, {skip_count} up-to-date, {err_count} errors, "
                f"{new_rows} new rows total. Scanner cache cleared."
            )
        )
