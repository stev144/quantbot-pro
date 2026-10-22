# claude code changed: new file — universe expansion mission, Step 3's
# explicit test requirement: "a test that fails loudly if the fetched end
# date is >7 days stale." This is the real regression guard for the bug
# fixed in bot/fetch_all_symbols.py (forward pagination from a fixed 2020
# START_DATE silently landed on a fixed, ever-more-stale cutoff regardless
# of when the script actually ran — confirmed via real on-disk data that
# had stopped updating).
#
# Reads the real, already-fetched CSVs in data/ (no mocking, per this
# project's own testing convention) rather than making network calls
# itself — freshness is a property of what's already on disk, not
# something to re-derive by fetching again inside a test.

import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase

from bot.fetch_all_symbols import SYMBOLS, FRESHNESS_MAX_AGE_DAYS, symbol_to_filename, OUTPUT_DIR


class FetchWindowKeepsInitWindowTest(SimpleTestCase):  # claude code changed: 2026-10-21 — new
    def test_window_never_starts_after_the_canonical_init_window(self):
        from bot.fetch_all_symbols import fetch_window_start
        from bot.quant_core.research_zones import ZONES
        init_start = ZONES.init_window()[0]
        span = 5 * 365 * 24
        for now in ("2026-09-20", "2026-10-21", "2028-01-01"):
            start = pd.Timestamp(fetch_window_start(pd.Timestamp(now, tz="UTC").to_pydatetime(), span))
            self.assertLessEqual(start, init_start, now)
        early = pd.Timestamp("2026-09-01", tz="UTC").to_pydatetime()            # before the drift point: rolling start is earlier
        self.assertEqual(pd.Timestamp(fetch_window_start(early, span)), pd.Timestamp(early) - pd.Timedelta(hours=span))


class DataFreshnessTest(SimpleTestCase):

    def test_every_tracked_symbols_data_is_within_freshness_window(self):
        # claude code changed: 2026-10-21 — owner decision D-8: fetch_all_symbols.seal_before_write() routes every
        # candle at/after the sealed forward holdout F (2026-10-04 00:00 UTC) to the sealed store, never to data/.
        # While F is sealed the newest candle research may hold is F − 1 h, so freshness is measured against
        # min(now, F); measuring against "now" made this test impossible to pass for the whole sealed period.
        from bot.quant_core.research_zones import ZONES
        now = min(datetime.now(tz=timezone.utc), ZONES.F.to_pydatetime())
        stale = []
        missing = []

        for symbol in SYMBOLS:
            filepath = OUTPUT_DIR / symbol_to_filename(symbol)
            if not filepath.exists():
                missing.append(symbol)
                continue

            df = pd.read_csv(filepath)
            if df.empty:
                missing.append(symbol)
                continue

            last_timestamp = pd.Timestamp(df["timestamp"].iloc[-1])
            if last_timestamp.tzinfo is None:
                last_timestamp = last_timestamp.tz_localize("UTC")

            staleness_days = (now - last_timestamp).total_seconds() / 86400
            if staleness_days > FRESHNESS_MAX_AGE_DAYS:
                stale.append(f"{symbol}: last candle {last_timestamp.isoformat()} ({staleness_days:.1f} days old)")

        # claude code changed: 2026-10-21 — a market the EXCHANGE has halted (Binance status != TRADING, e.g. STORJUSDT
        # "BREAK" since 2026-09-03) cannot produce new candles; that is a real market condition, not a fetch failure.
        # Checked live (project convention: real endpoints, no mocks) and reported, never silently dropped; any stale
        # symbol whose market IS trading still fails.
        halted = []
        if stale:
            import ccxt
            markets = ccxt.binance().load_markets()
            still_stale = []
            for line in stale:
                sym = line.split(":")[0]
                m = markets.get(sym)
                if m is None or not m.get("active", False):
                    halted.append(f"{line} — Binance market not trading ({(m or {}).get('info', {}).get('status', 'not listed')})")
                else:
                    still_stale.append(line)
            stale = still_stale
        if halted:
            print("\n[freshness] halted/unlisted markets (not failures):\n  " + "\n  ".join(halted))
        self.assertFalse(
            stale,
            "The following symbols have stale data (older than "
            f"{FRESHNESS_MAX_AGE_DAYS} days) — re-run `python bot/fetch_all_symbols.py`:\n"
            + "\n".join(stale),
        )
        # claude code changed: a symbol with no CSV at all is a separate,
        # softer condition from staleness (e.g. a newly-added symbol not
        # yet fetched) — recorded but not failed here, since Step 5's own
        # composition tests (test_universe_selection.py) are what enforce
        # universe membership; this test is specifically about the
        # freshness of data that DOES exist.
        if missing:
            import warnings
            warnings.warn(f"{len(missing)} tracked symbols have no fetched CSV yet: {missing}")
