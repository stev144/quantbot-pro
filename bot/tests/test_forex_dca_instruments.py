# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_forex_dca_instruments.py
# Phase 3 — Forex DCA instrument tests
#
# claude code changed: new file — 2026-10-16. Tests for all Workstream A–G deliverables:
# ForexInstrumentSpec, forex_accounting, ForexCostProfile, is_expected_gap, validate_ohlc_candle,
# build_forex_universe, and crypto DCA regression.
#
# All financial known-answer tests include an independent, hand-computed reference calculation
# in the test docstring. Tests are read-only: no I/O, no network, no exchange connections.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

import math
from django.test import SimpleTestCase

import pandas as pd
import numpy as np


class ForexInstrumentSpecTests(SimpleTestCase):
    """Workstream A — ForexInstrumentSpec construction and is_cost_complete()."""

    def test_eurusd_instrument_spec_valid(self):  # claude code changed: new
        """Placeholder EURUSD spec instantiates with the right fields; is_cost_complete() returns False
        because swap_long/swap_short are None (not yet from a real broker)."""
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD
        spec = PLACEHOLDER_EURUSD
        self.assertEqual(spec.canonical_symbol, "EUR/USD")
        self.assertEqual(spec.pip_size, 0.0001)
        self.assertEqual(spec.contract_size, 100_000.0)
        self.assertEqual(spec.account_currency, "USD")
        self.assertFalse(spec.validated)
        self.assertFalse(spec.is_cost_complete(),
                         "placeholder spec must not claim cost_complete=True: swap rates are None")

    def test_usdjpy_pip_size_is_0_01(self):  # claude code changed: new — JPY-quoted pair convention
        """USD/JPY uses pip_size=0.01 (3-decimal quote), not 0.0001."""
        from bot.research.forex_instrument_spec import PLACEHOLDER_USDJPY
        self.assertEqual(PLACEHOLDER_USDJPY.pip_size, 0.01)
        self.assertEqual(PLACEHOLDER_USDJPY.price_precision, 3)

    def test_cost_complete_requires_all_four_fields(self):  # claude code changed: new
        """is_cost_complete() returns False unless spread, commission, swap_long AND swap_short are set."""
        from bot.research.forex_instrument_spec import ForexInstrumentSpec
        # Missing swap_short → incomplete
        spec = ForexInstrumentSpec(
            canonical_symbol="EUR/USD",
            pip_size=0.0001,
            contract_size=100_000.0,
            spread_typical_pips=1.0,
            commission_per_lot=0.0,
            swap_long=-0.5,
            swap_short=None,   # missing
        )
        self.assertFalse(spec.is_cost_complete())

        # All four set → complete
        full = ForexInstrumentSpec(
            canonical_symbol="EUR/USD",
            pip_size=0.0001,
            contract_size=100_000.0,
            spread_typical_pips=1.0,
            commission_per_lot=0.0,
            swap_long=-0.5,
            swap_short=0.3,
        )
        self.assertTrue(full.is_cost_complete())

    def test_invalid_asset_class_raises(self):  # claude code changed: new
        """Constructing ForexInstrumentSpec with a non-FOREX asset_class raises ValueError."""
        from bot.research.forex_instrument_spec import ForexInstrumentSpec
        with self.assertRaises(ValueError):
            ForexInstrumentSpec(canonical_symbol="BTC/USDT", asset_class="CRYPTO", pip_size=1.0)

    def test_pip_value_in_quote_eurusd_1lot(self):  # claude code changed: new — known-answer
        """EUR/USD pip value: 0.0001 × 100,000 × 1 lot = $10 USD per pip.
        Hand calculation: pip_size=0.0001, contract_size=100,000, lots=1 → $10"""
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD
        self.assertAlmostEqual(PLACEHOLDER_EURUSD.pip_value_in_quote(1.0), 10.0, places=10)

    def test_pip_value_in_quote_returns_none_when_fields_missing(self):  # claude code changed: new
        from bot.research.forex_instrument_spec import ForexInstrumentSpec
        spec = ForexInstrumentSpec(canonical_symbol="EUR/USD")
        self.assertIsNone(spec.pip_value_in_quote())


class ForexAccountingTests(SimpleTestCase):
    """Workstream B — Position sizing and P&L accounting."""

    def test_eurusd_pip_pnl_known_answer(self):  # claude code changed: new — hand-computed reference
        """EUR/USD LONG 1 lot: entry=1.1000, exit=1.1010 → +10 pips → P&L in quote currency = +$100.
        Independent hand calculation:
            base_qty = 1 lot × 100,000 = 100,000 EUR
            P&L_quote = (1.1010 − 1.1000) × 100,000 = 0.0010 × 100,000 = $100.00 USD
        """
        from bot.research.forex_accounting import pnl_quote_currency
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD
        pnl = pnl_quote_currency(
            side="LONG",
            base_qty=1.0 * PLACEHOLDER_EURUSD.contract_size,   # 100,000 EUR
            entry_price=1.1000,
            exit_price=1.1010,
            spec=PLACEHOLDER_EURUSD,
        )
        self.assertAlmostEqual(pnl, 100.0, places=8)

    def test_usdjpy_pip_pnl_known_answer(self):  # claude code changed: new — JPY-quoted currency conversion
        """USD/JPY SHORT 1 lot: entry=150.00, exit=149.50 → +50 pips → P&L in JPY → convert to USD.
        Independent hand calculation:
            base_qty = 1 lot × 100,000 = 100,000 USD
            P&L_quote (JPY) = (150.00 − 149.50) × 100,000 = 0.50 × 100,000 = ¥50,000 JPY
            quote_to_account_rate for JPY→USD = 150.00 (use ENTRY rate, causal)
            P&L_account (USD) = 50,000 / 150.00 = $333.333... USD
        """
        from bot.research.forex_accounting import pnl_quote_currency, pnl_account_currency
        from bot.research.forex_instrument_spec import PLACEHOLDER_USDJPY
        base_qty = 1.0 * PLACEHOLDER_USDJPY.contract_size  # 100,000 USD
        pnl_jpy = pnl_quote_currency("SHORT", base_qty, 150.00, 149.50, PLACEHOLDER_USDJPY)
        self.assertAlmostEqual(pnl_jpy, 50_000.0, places=6)
        pnl_usd = pnl_account_currency(pnl_jpy, quote_to_account_rate=150.00)
        self.assertAlmostEqual(pnl_usd, 50_000.0 / 150.00, places=8)

    def test_lots_from_allocation_eurusd(self):  # claude code changed: new — known-answer lot sizing
        """1000 USD allocated, leverage 30:1, entry 1.1000, contract_size=100,000 EUR.
        Independent hand calculation:
            notional = 1000 × 30 = $30,000
            base_units = 30,000 / 1.1000 = 27,272.727... EUR
            raw_lots = 27,272.727 / 100,000 = 0.27272...
            floor to 0.01 increment → 0.27 lots
        """
        from bot.research.forex_accounting import lots_from_allocation
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD
        lots = lots_from_allocation(
            allocated_capital=1000.0,
            entry_price=1.1000,
            spec=PLACEHOLDER_EURUSD,
            leverage=30.0,
        )
        # Expected: floor(27272.727/100000 / 0.01) × 0.01 = floor(0.2727..) rounded = 0.27
        expected = math.floor(30_000 / 1.1000 / 100_000 / 0.01) * 0.01
        self.assertAlmostEqual(lots, expected, places=8)
        self.assertGreater(lots, 0.0)

    def test_lots_from_allocation_rejects_nonpositive_inputs(self):  # claude code changed: new
        from bot.research.forex_accounting import lots_from_allocation
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD
        with self.assertRaises(ValueError):
            lots_from_allocation(-100.0, 1.1, PLACEHOLDER_EURUSD, 30.0)
        with self.assertRaises(ValueError):
            lots_from_allocation(100.0, 0.0, PLACEHOLDER_EURUSD, 30.0)
        with self.assertRaises(ValueError):
            lots_from_allocation(100.0, 1.1, PLACEHOLDER_EURUSD, 0.0)

    def test_weighted_average_entry_two_fills(self):  # claude code changed: new — known-answer WAE
        """Fill 1: 0.5 lots at 1.1000; Fill 2: 0.5 lots at 1.0980.
        Independent hand calculation:
            WAE = (1.1000 × 0.5 + 1.0980 × 0.5) / (0.5 + 0.5)
                = (0.5500 + 0.5490) / 1.0
                = 1.0990
        """
        from bot.research.forex_accounting import weighted_average_entry
        wae = weighted_average_entry([(1.1000, 0.5), (1.0980, 0.5)])
        self.assertAlmostEqual(wae, 1.0990, places=10)

    def test_weighted_average_entry_single_fill(self):  # claude code changed: new — edge case
        from bot.research.forex_accounting import weighted_average_entry
        self.assertAlmostEqual(weighted_average_entry([(1.2345, 1.0)]), 1.2345, places=10)

    def test_weighted_average_entry_empty_raises(self):  # claude code changed: new
        from bot.research.forex_accounting import weighted_average_entry
        with self.assertRaises(ValueError):
            weighted_average_entry([])

    def test_gross_to_net_pnl_accounting_identity(self):  # claude code changed: new — accounting identity
        """Net P&L = gross − commission − spread − financing.
        Hand calculation: gross=200.0, commission=5.0, spread=2.5, financing=1.5 → net=191.0"""
        from bot.research.forex_accounting import gross_to_net_pnl
        net = gross_to_net_pnl(
            gross_pnl=200.0,
            commission_paid=5.0,
            spread_cost=2.5,
            financing_cost=1.5,
        )
        self.assertAlmostEqual(net, 191.0, places=10)

    def test_gross_to_net_pnl_negative_cost_raises(self):  # claude code changed: new — sign guard
        from bot.research.forex_accounting import gross_to_net_pnl
        with self.assertRaises(ValueError):
            gross_to_net_pnl(100.0, commission_paid=-1.0, spread_cost=0.0, financing_cost=0.0)

    def test_pnl_quote_invalid_side_raises(self):  # claude code changed: new
        from bot.research.forex_accounting import pnl_quote_currency
        with self.assertRaises(ValueError):
            pnl_quote_currency("BUY", 100_000, 1.1, 1.2)

    def test_short_pnl_is_negative_when_price_rises(self):  # claude code changed: new — sign correctness
        """A SHORT position loses money when price rises (adverse move).
        SHORT entry=1.1000, exit=1.1100: price rose 0.0100 → P&L = (1.1000-1.1100)*100,000 = -1000"""
        from bot.research.forex_accounting import pnl_quote_currency
        pnl = pnl_quote_currency("SHORT", 100_000.0, 1.1000, 1.1100)
        self.assertAlmostEqual(pnl, -1000.0, places=8)


class ForexCostProfileTests(SimpleTestCase):
    """Workstream C — ForexCostProfile and get_forex_execution_costs()."""

    def test_incomplete_cost_profile_blocks_claims(self):  # claude code changed: new
        """ForexCostProfile with swap=None → cost_complete=False."""
        from bot.config.execution_costs import ForexCostProfile
        profile = ForexCostProfile(
            instrument_id="EUR/USD",
            spread_pips=1.0,
            commission_per_lot=0.0,
            swap_long_pips_per_day=None,    # missing
            swap_short_pips_per_day=None,   # missing
        )
        self.assertFalse(profile.cost_complete)

    def test_complete_cost_profile(self):  # claude code changed: new
        from bot.config.execution_costs import ForexCostProfile
        profile = ForexCostProfile(
            instrument_id="EUR/USD",
            spread_pips=1.0,
            commission_per_lot=0.0,
            swap_long_pips_per_day=-0.7,
            swap_short_pips_per_day=0.2,
        )
        self.assertTrue(profile.cost_complete)

    def test_get_forex_execution_costs_placeholder_is_incomplete(self):  # claude code changed: new
        """get_forex_execution_costs() for an unregistered pair returns a placeholder profile
        with cost_complete=False — never claims completeness for an unknown instrument."""
        from bot.config.execution_costs import get_forex_execution_costs
        profile = get_forex_execution_costs("EUR/USD")
        self.assertFalse(profile.cost_complete)

    def test_crypto_costs_unchanged(self):  # claude code changed: new — regression guard
        """The existing FEE_RATE and SLIPPAGE_RATE are unchanged by the Workstream C additions."""
        from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE, get_venue_execution_costs
        self.assertEqual(FEE_RATE, 0.001)
        self.assertEqual(SLIPPAGE_RATE, 0.0005)
        costs = get_venue_execution_costs("binance")
        self.assertEqual(costs["fee_rate"], 0.001)
        self.assertEqual(costs["slippage_rate"], 0.0005)


class ForexDataValidationTests(SimpleTestCase):
    """Workstream D — Session-aware gap detection and OHLC validation."""

    def _ts(self, s):
        return pd.Timestamp(s, tz="UTC")

    def test_weekend_gap_is_expected(self):  # claude code changed: new
        """Friday 22:00 UTC → Monday 22:00 UTC is a fully scheduled closure under OD-FX-1.
        The entire gap (Sat–Sun) falls within the FX calendar's weekend closure."""
        from bot.research.forex_data_validation import is_expected_gap
        # Standard winter weekend: closes Fri 22:00 UTC, reopens Sun 22:00 UTC
        result = is_expected_gap(
            self._ts("2022-01-07 21:00"),  # Fri 21:00 UTC (last observed candle)
            self._ts("2022-01-09 22:00"),  # Sun 22:00 UTC (first reopening candle)
        )
        self.assertTrue(result)

    def test_intraweek_gap_is_not_expected(self):  # claude code changed: new
        """Monday 09:00 → Monday 11:00 UTC: two missing trading hours → NOT an expected gap."""
        from bot.research.forex_data_validation import is_expected_gap
        result = is_expected_gap(
            self._ts("2022-01-10 09:00"),   # Mon 09:00 UTC
            self._ts("2022-01-10 11:00"),   # Mon 11:00 UTC — two trading hours missing
        )
        self.assertFalse(result)

    def test_consecutive_candles_always_expected(self):  # claude code changed: new — edge case
        """Consecutive candles (1-hour gap) have no missing hours → always expected."""
        from bot.research.forex_data_validation import is_expected_gap
        result = is_expected_gap(
            self._ts("2022-01-10 09:00"),
            self._ts("2022-01-10 10:00"),
        )
        self.assertTrue(result)

    def test_prev_ts_must_be_before_curr_ts(self):  # claude code changed: new
        from bot.research.forex_data_validation import is_expected_gap
        with self.assertRaises(ValueError):
            is_expected_gap(
                self._ts("2022-01-10 10:00"),
                self._ts("2022-01-10 09:00"),
            )

    def test_valid_ohlc_returns_no_errors(self):  # claude code changed: new
        from bot.research.forex_data_validation import validate_ohlc_candle
        row = {"open": 1.1000, "high": 1.1020, "low": 1.0980, "close": 1.1010}
        errors = validate_ohlc_candle(row)
        self.assertEqual(errors, [])

    def test_invalid_ohlc_high_less_than_low_rejected(self):  # claude code changed: new
        from bot.research.forex_data_validation import validate_ohlc_candle
        row = {"open": 1.1000, "high": 1.0950, "low": 1.1020, "close": 1.1010}  # high < low
        errors = validate_ohlc_candle(row)
        self.assertTrue(any("high" in e and "low" in e for e in errors), errors)

    def test_invalid_ohlc_negative_price_rejected(self):  # claude code changed: new
        from bot.research.forex_data_validation import validate_ohlc_candle
        row = {"open": -1.0, "high": 1.1, "low": 0.9, "close": 1.0}
        errors = validate_ohlc_candle(row)
        self.assertTrue(any("open" in e for e in errors), errors)

    def test_missing_column_detected(self):  # claude code changed: new
        from bot.research.forex_data_validation import validate_ohlc_candle
        row = {"open": 1.1, "high": 1.2, "close": 1.1}   # missing 'low'
        errors = validate_ohlc_candle(row)
        self.assertTrue(any("low" in e for e in errors), errors)

    def test_open_outside_high_low_range_detected(self):  # claude code changed: new — candle coherence
        from bot.research.forex_data_validation import validate_ohlc_candle
        row = {"open": 1.1100, "high": 1.1050, "low": 1.0950, "close": 1.1000}  # open > high
        errors = validate_ohlc_candle(row)
        self.assertTrue(any("open" in e for e in errors), errors)


class ForexUniverseTests(SimpleTestCase):
    """Workstream E — Forex universe validation."""

    def test_target_pairs_pass_universe_checks_without_data(self):  # claude code changed: new
        """The five target DCA pairs (EUR/USD etc.) pass FX_UNIVERSE and instrument registry checks
        even without data on disk (require_data=False)."""
        from bot.research.forex_universe import build_dca_target_universe
        accepted, rejected = build_dca_target_universe(require_data=False)
        self.assertEqual(len(rejected), 0, f"unexpected rejections: {rejected}")
        self.assertEqual(len(accepted), 5)

    def test_non_fx_universe_pair_rejected(self):  # claude code changed: new
        """A pair not in FX_UNIVERSE (OD-FX-7) must be rejected."""
        from bot.research.forex_universe import build_forex_universe
        accepted, rejected = build_forex_universe(["USD/HKD"], require_data=False)
        self.assertEqual(accepted, [])
        self.assertIn("USD/HKD", rejected)

    def test_crypto_symbol_rejected(self):  # claude code changed: new — asset class gate
        from bot.research.forex_universe import build_forex_universe
        accepted, rejected = build_forex_universe(["BTC/USDT"], require_data=False)
        self.assertEqual(accepted, [])
        self.assertTrue(len(rejected) == 1)


class CryptoDcaRegressionTests(SimpleTestCase):
    """Workstream F — Crypto DCA regression: shared code changes must not alter existing crypto behaviour."""

    def test_crypto_dca_simulation_unchanged(self):  # claude code changed: new — regression guard
        """simulate_dca_cycles() on a synthetic crypto series returns consistent results after all
        Workstream A–G additions. The key invariant: the execution costs must match the pre-change
        values (FEE_RATE=0.001, SLIPPAGE_RATE=0.0005). No new imports should have broken the module."""
        from bot.research import dca_engine as de
        from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE

        # Verify cost constants unchanged
        self.assertEqual(de.BASELINE_CONFIG.fee_rate, FEE_RATE)
        self.assertEqual(de.BASELINE_CONFIG.slippage_rate, SLIPPAGE_RATE)

        # Run a small deterministic simulation — should complete without error
        idx = pd.date_range("2022-01-01", periods=200, freq="h", tz="UTC")
        rng = np.random.default_rng(99)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
        o = np.r_[c[0], c[:-1]]
        df = pd.DataFrame({
            "open": o, "close": c,
            "high": np.maximum(o, c),
            "low": np.minimum(o, c),
        }, index=idx)
        trades = de.simulate_dca_cycles(df, de.BASELINE_CONFIG)
        self.assertIsInstance(trades, list)
        summary = de.summarize_dca_cycles(trades, de.BASELINE_CONFIG)
        self.assertIn("n_cycles", summary)
        self.assertGreater(summary["n_cycles"], 0)

    def test_dca_run_provenance_accepts_instrument_spec(self):  # claude code changed: new — Workstream G
        """dca_run_provenance() with an instrument_spec records spec version and cost_complete."""
        from bot.research import dca_engine as de
        from bot.research.forex_instrument_spec import PLACEHOLDER_EURUSD

        idx = pd.date_range("2022-01-01", periods=100, freq="h", tz="UTC")
        rng = np.random.default_rng(42)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100)))
        df = pd.DataFrame({"close": c, "open": c, "high": c, "low": c}, index=idx)

        prov = de.dca_run_provenance(df, de.BASELINE_CONFIG, kind="TEST_RUN", instrument_spec=PLACEHOLDER_EURUSD)
        self.assertIn("instrument_spec_version", prov)
        self.assertIn("instrument_spec_cost_complete", prov)
        self.assertFalse(prov["instrument_spec_cost_complete"],
                         "placeholder spec must report cost_complete=False in provenance")

    def test_dca_run_provenance_without_spec_unchanged(self):  # claude code changed: new — backward compat
        """dca_run_provenance() without instrument_spec works exactly as before."""
        from bot.research import dca_engine as de
        idx = pd.date_range("2022-01-01", periods=100, freq="h", tz="UTC")
        rng = np.random.default_rng(42)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100)))
        df = pd.DataFrame({"close": c, "open": c, "high": c, "low": c}, index=idx)
        prov = de.dca_run_provenance(df, de.BASELINE_CONFIG, kind="TEST_RUN")
        self.assertNotIn("instrument_spec_version", prov)
        self.assertIn("run_id", prov)


class ForexDcaBlockedGateTests(SimpleTestCase):
    """Verify the DCA engine correctly blocks FX data under the 24/7 crypto rules
    and that the block is by design, not a bug."""

    def test_fx_data_rejected_by_assert_continuous_market(self):  # claude code changed: new
        """FX data (with weekend gaps) is correctly rejected by assert_continuous_market(),
        which is the INTENDED safety gate (Decision 5 / FOREX_PROFILE requirement)."""
        from bot.quant_core.market_profile import AssetClassMismatch, assert_continuous_market

        # Simulate an FX-like index with weekend gaps (missing Saturdays)
        # 6 weeks of hourly candles — need ≥ 4 inner Saturdays for assert_continuous_market's
        # min_saturdays=4 guard; 4 weeks only yields 3 Saturdays within the inner span
        all_hours = pd.date_range("2022-01-03", periods=6 * 7 * 24, freq="h", tz="UTC")  # claude code changed: 6 weeks not 4 — ≥4 inner Saturdays required
        # Keep only Mon–Fri hours (drop weekend Sat/Sun) — simulates FX data
        fx_like = all_hours[all_hours.day_of_week < 5]

        # Must raise AssetClassMismatch — this is the designed safety gate
        with self.assertRaises(AssetClassMismatch):
            assert_continuous_market(fx_like, purpose="test")

    def test_forex_profile_is_canonical(self):  # claude code changed: 2026-10-16 — renamed from test_forex_profile_not_canonical_yet; all four owner decisions (OD-FX-HB-1, OD-FX-FS-1, OD-FX-CM-1, FX-DCA-PROTOCOL-1) are now locked; gate is now OPEN, not blocked
        """FOREX_PROFILE.require_canonical() no longer raises — all four owner decisions are recorded.
        This test verifies that the gate is correctly OPEN and all required fields are populated."""
        from bot.quant_core.market_profile import FOREX_PROFILE, OwnerDecisionMissing
        # Must not raise — all four decisions are now locked
        profile = FOREX_PROFILE.require_canonical("test")
        self.assertIs(profile, FOREX_PROFILE)
        # All decision fields are populated
        missing = FOREX_PROFILE.missing_decisions()
        self.assertEqual(missing, [], f"Expected no missing decisions, got: {missing}")
        self.assertTrue(FOREX_PROFILE.canonical_capable)
        # Key field values match the approved owner decisions
        self.assertIn("TRADING_CANDLE_COUNT", FOREX_PROFILE.horizon_basis)
        self.assertIn("OD-FX-HB-1", FOREX_PROFILE.horizon_basis)
        self.assertIn("MT5_BROKER_STATIC", FOREX_PROFILE.financing_source)
        self.assertIn("OD-FX-FS-1", FOREX_PROFILE.financing_source)
        self.assertIn("MT5_BROKER_PUBLISHED", FOREX_PROFILE.cost_model)
        self.assertIn("OD-FX-CM-1", FOREX_PROFILE.cost_model)
        # Protocol hash matches the approved FX-DCA-PROTOCOL-1 document
        self.assertEqual(FOREX_PROFILE.protocol_hash,
                         "9d2fec9929e7123bd8b6a47d57b142f979013680ef77eeb6d61f45747a8ab54f")  # claude code changed: SHA-256 of FX_DCA_PROTOCOL_2026-10-16.md
