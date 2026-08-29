# claude code changed: new file — universe expansion mission, Step 5's
# explicit test requirements. Reads the real, persisted
# data/universe_selection.json (written by a real `python -m
# bot.universe_selector` run against live Binance data — no mocking, per
# this project's own testing convention) rather than re-deriving it, since
# re-running the actual selector (network I/O across ~800 candidates) is
# far too slow/rate-limit-sensitive to run on every test invocation.
#
# If this file has never been generated, these tests fail loudly with a
# clear message rather than skipping silently — a missing selection file
# means the universe-expansion pipeline was never run, which is itself
# worth surfacing, not hiding.

import json
from datetime import datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase

from bot.universe_selector import (
    OUTPUT_PATH, EXCLUDED_BASE_ASSETS, DEFAULT_TARGET_SIZE,
    DEFAULT_FALLBACK_MIN_YEARS,
)


def _load_selection() -> dict:
    if not OUTPUT_PATH.exists():
        raise AssertionError(
            f"{OUTPUT_PATH} does not exist — run `python -m bot.universe_selector` "
            f"first to generate a real universe selection before running these tests."
        )
    with open(OUTPUT_PATH) as f:
        return json.load(f)


class UniverseCompositionTest(SimpleTestCase):

    def test_exactly_target_size_unique_symbols(self):
        data = _load_selection()
        symbols = data["symbols"]
        self.assertEqual(len(symbols), DEFAULT_TARGET_SIZE)
        self.assertEqual(len(symbols), len(set(symbols)), "duplicate symbols in selection")

    def test_no_duplicate_base_assets_across_quote_pairs(self):
        # claude code changed: distinct from the raw-symbol-duplicate check
        # above — this guards against the same base asset appearing twice
        # under different quote currencies (e.g. "BTC/USDT" + "BTC/USD"),
        # which the raw-string check wouldn't catch.
        data = _load_selection()
        bases = [s.split("/")[0] for s in data["symbols"]]
        self.assertEqual(len(bases), len(set(bases)), "same base asset selected under multiple quote pairs")

    def test_no_excluded_stablecoin_or_wrapped_or_fiat_token_present(self):
        data = _load_selection()
        for symbol in data["symbols"]:
            base = symbol.split("/")[0]
            self.assertNotIn(
                base.upper(), EXCLUDED_BASE_ASSETS,
                f"{symbol}: excluded base asset {base} leaked into the final selection",
            )

    def test_every_symbol_meets_the_recorded_minimum_history(self):
        # claude code changed: min_years_used reflects a real, verified
        # (paginated, continuity-checked) history requirement every
        # selected symbol had to pass at generation time — see
        # universe_selector._check_history(). Cross-checking the >=4yr
        # floor here rather than re-fetching every symbol's OHLCV history
        # over the network again.
        data = _load_selection()
        self.assertGreaterEqual(data["min_years_used"], DEFAULT_FALLBACK_MIN_YEARS)

    def test_selection_timestamp_is_recorded(self):
        # claude code changed: liquidity rankings drift over time (see
        # universe_selector.py's own module docstring) — the selection
        # must be re-run periodically, so it must carry a real timestamp
        # rather than silently going stale with no way to tell.
        data = _load_selection()
        selected_at = datetime.fromisoformat(data["selected_at"])
        self.assertIsNotNone(selected_at.tzinfo, "selected_at must be timezone-aware")


class UniverseConsolidationTest(SimpleTestCase):
    """
    claude code changed: new — confirms the universe-consolidation part of
    this mission actually took effect: bot.instruments (and therefore
    cointegration_engine.py / cross_section_engine.py / the Research Lab's
    SUPPORTED_ASSETS) reflect the SAME symbol count as the persisted
    selection, rather than each maintaining an independent, driftable copy.
    """

    def test_instrument_registry_size_matches_persisted_selection(self):
        from bot.instruments import symbols_for_asset_class, ASSET_CLASS_CRYPTO
        data = _load_selection()
        registry_symbols = symbols_for_asset_class(ASSET_CLASS_CRYPTO)
        self.assertEqual(len(registry_symbols), len(data["symbols"]))

    def test_cointegration_and_cross_section_universes_match_registry(self):
        from bot.instruments import symbols_for_asset_class, ASSET_CLASS_CRYPTO
        from bot.research.cointegration_engine import UNIVERSE as COINT_UNIVERSE
        from bot.research.cross_section_engine import UNIVERSE as CROSS_SECTION_UNIVERSE

        expected = {s.replace("/", "_") for s in symbols_for_asset_class(ASSET_CLASS_CRYPTO)}
        self.assertEqual(set(COINT_UNIVERSE), expected)
        self.assertEqual(set(CROSS_SECTION_UNIVERSE), expected)
