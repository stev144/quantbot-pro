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


class DataFreshnessTest(SimpleTestCase):

    def test_every_tracked_symbols_data_is_within_freshness_window(self):
        now = datetime.now(tz=timezone.utc)
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
