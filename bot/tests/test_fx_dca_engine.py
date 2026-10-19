"""
bot/tests/test_fx_dca_engine.py
Tests for FxDcaConfig and simulate_dca_path_fx (F1 + F3 remediation).

Verifies:
  1. FxDcaConfig validates fee_rate=0 / slippage_rate=0 (rejects non-zero)
  2. FxDcaConfig.is_cost_complete() returns False when any cost field is None
  3. FxDcaConfig.to_dca_config() produces a DCAConfig with fee_rate=0 / slippage_rate=0
  4. simulate_dca_path_fx() rejects non-FOREX market profiles (AssetClassMismatch)
  5. simulate_dca_path_fx() rejects missing required columns
  6. simulate_dca_path_fx() rejects duplicate timestamps
  7. simulate_dca_path_fx() rejects OHLC violations
  8. simulate_dca_path_fx() runs on clean calibration-only FX data (pre-holdout)
  9. simulate_dca_path_fx() returns (trades, mtm_frame) with expected structure
  10. simulate_dca_path_fx() does NOT require assert_continuous_market (FX weekend gaps are OK)
  11. FX_BASELINE_CONFIG has fee_rate=0 and slippage_rate=0
  12. Trades from simulate_dca_path_fx() carry fx_cost_label == "COST_INCOMPLETE"
     when costs are None

These tests do NOT examine holdout data and do NOT run any simulation on rows
at or after 2024-12-24 00:00 UTC.
"""
# claude code changed: new file — 2026-10-16; F1/F3 tests for FxDcaConfig + simulate_dca_path_fx

from __future__ import annotations

from datetime import timezone
from unittest import TestCase

import pandas as pd

from bot.research.dca_engine import (
    FxDcaConfig,
    FX_BASELINE_CONFIG,
    simulate_dca_path_fx,
    BASELINE_LEVELS,
    DCALevel,
    FILL_MODEL_NEXT_OPEN,
    FILL_MODEL_LEGACY_CLOSE,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_fx_df(start: str = "2021-01-01", periods: int = 500, freq: str = "1h") -> pd.DataFrame:
    """Minimal 1h FX OHLC DataFrame with UTC timestamps, entirely before holdout."""  # claude code changed: test helper
    ts = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    price = 1.1000 + (pd.RangeIndex(periods) * 0.00001)
    return pd.DataFrame({
        "timestamp": ts,
        "open": price,
        "high": price + 0.0002,
        "low": price - 0.0002,
        "close": price + 0.0001,
        "volume": 100,
    })


def _make_fx_df_with_weekend_gaps(periods: int = 200) -> pd.DataFrame:
    """
    FX DataFrame with realistic weekend gaps (Fri 22:00 → Mon 00:00 UTC).

    Simulates session-traded data: skips Sat/Sun, so the resulting index
    has irregular spacing — exactly what assert_continuous_market rejects
    but simulate_dca_path_fx must accept.
    """  # claude code changed: test helper for FX weekend gap acceptance
    ts_full = pd.date_range("2022-01-03 00:00", periods=periods * 3, freq="1h", tz="UTC")
    # Keep only Mon-Fri (weekday 0-4)
    ts_fx = ts_full[ts_full.dayofweek < 5][:periods]
    price = 1.1000 + (pd.RangeIndex(len(ts_fx)) * 0.00001)
    return pd.DataFrame({
        "timestamp": ts_fx,
        "open": price,
        "high": price + 0.0002,
        "low": price - 0.0002,
        "close": price + 0.0001,
        "volume": 100,
    })


# ── FxDcaConfig tests ─────────────────────────────────────────────────────────

class FxDcaConfigValidationTests(TestCase):  # claude code changed: new test class — FxDcaConfig validation
    def test_default_construction_succeeds(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS)
        self.assertEqual(cfg.fee_rate, 0.0)
        self.assertEqual(cfg.slippage_rate, 0.0)

    def test_nonzero_fee_rate_raises(self):
        with self.assertRaises(ValueError) as cm:
            FxDcaConfig(levels=BASELINE_LEVELS, fee_rate=0.001)
        self.assertIn("fee_rate", str(cm.exception).lower())

    def test_nonzero_slippage_rate_raises(self):
        with self.assertRaises(ValueError) as cm:
            FxDcaConfig(levels=BASELINE_LEVELS, slippage_rate=0.0005)
        self.assertIn("slippage_rate", str(cm.exception).lower())

    def test_invalid_direction_raises(self):
        with self.assertRaises(ValueError):
            FxDcaConfig(levels=BASELINE_LEVELS, direction="BUY")

    def test_valid_direction_long(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS, direction="LONG")
        self.assertEqual(cfg.direction, "LONG")

    def test_valid_direction_short(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS, direction="SHORT")
        self.assertEqual(cfg.direction, "SHORT")

    def test_valid_fill_model_next_open(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS, fill_model=FILL_MODEL_NEXT_OPEN)
        self.assertEqual(cfg.fill_model, FILL_MODEL_NEXT_OPEN)

    def test_valid_fill_model_legacy_close(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS, fill_model=FILL_MODEL_LEGACY_CLOSE)
        self.assertEqual(cfg.fill_model, FILL_MODEL_LEGACY_CLOSE)

    def test_intrabar_ladder_fill_model_rejected(self):
        """INTRABAR_LADDER is not supported for FX."""
        with self.assertRaises(ValueError):
            FxDcaConfig(levels=BASELINE_LEVELS, fill_model="INTRABAR_LADDER")


class FxDcaConfigCostCompletenessTests(TestCase):  # claude code changed: new test class — is_cost_complete
    def test_all_none_is_not_complete(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS)
        self.assertFalse(cfg.is_cost_complete())

    def test_partial_none_is_not_complete(self):
        cfg = FxDcaConfig(
            levels=BASELINE_LEVELS,
            spread_pips=1.0,
            commission_per_lot=None,
            swap_long_pips=-3.5,
            swap_short_pips=1.2,
        )
        self.assertFalse(cfg.is_cost_complete())

    def test_all_filled_is_complete(self):
        cfg = FxDcaConfig(
            levels=BASELINE_LEVELS,
            spread_pips=1.0,
            commission_per_lot=0.0,   # explicit zero = zero-commission confirmed
            swap_long_pips=-3.5,
            swap_short_pips=1.2,
        )
        self.assertTrue(cfg.is_cost_complete())

    def test_zero_commission_is_complete(self):
        """commission_per_lot=0.0 means confirmed zero, not unknown."""
        cfg = FxDcaConfig(
            levels=BASELINE_LEVELS,
            spread_pips=1.5,
            commission_per_lot=0.0,
            swap_long_pips=-2.0,
            swap_short_pips=0.5,
        )
        self.assertTrue(cfg.is_cost_complete())


class FxDcaConfigToDcaConfigTests(TestCase):  # claude code changed: new test class — to_dca_config conversion
    def test_fee_rate_is_zero_in_dca_config(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS)
        dca = cfg.to_dca_config()
        self.assertEqual(dca.fee_rate, 0.0)

    def test_slippage_rate_is_zero_in_dca_config(self):
        cfg = FxDcaConfig(levels=BASELINE_LEVELS)
        dca = cfg.to_dca_config()
        self.assertEqual(dca.slippage_rate, 0.0)

    def test_strategy_params_preserved(self):
        cfg = FxDcaConfig(
            levels=BASELINE_LEVELS,
            initial_usdt=10.0,
            profit_activation_pct=0.03,
            trailing_pct=0.01,
            direction="SHORT",
        )
        dca = cfg.to_dca_config()
        self.assertEqual(dca.initial_usdt, 10.0)
        self.assertEqual(dca.profit_activation_pct, 0.03)
        self.assertEqual(dca.trailing_pct, 0.01)
        self.assertEqual(dca.direction, "SHORT")


# ── FX_BASELINE_CONFIG tests ──────────────────────────────────────────────────

class FxBaselineConfigTests(TestCase):  # claude code changed: new test class — FX_BASELINE_CONFIG module constant
    def test_fee_rate_is_zero(self):
        self.assertEqual(FX_BASELINE_CONFIG.fee_rate, 0.0)

    def test_slippage_rate_is_zero(self):
        self.assertEqual(FX_BASELINE_CONFIG.slippage_rate, 0.0)

    def test_is_not_cost_complete(self):
        """FX_BASELINE_CONFIG must report COST_INCOMPLETE until specs are filled."""
        self.assertFalse(FX_BASELINE_CONFIG.is_cost_complete())

    def test_has_baseline_levels(self):
        self.assertEqual(FX_BASELINE_CONFIG.levels, BASELINE_LEVELS)


# ── simulate_dca_path_fx tests ────────────────────────────────────────────────

class SimulateFxPathAssetClassTests(TestCase):  # claude code changed: new — asset class guard (F1)
    def test_rejects_crypto_market_profile(self):
        """AssetClassMismatch when the profile is not FOREX."""
        from bot.quant_core.market_profile import AssetClassMismatch
        # Import a non-FX profile or construct a minimal mock
        # We use a simple namespace object with asset_class="CRYPTO"
        class _FakeProfile:
            asset_class = "CRYPTO"

        df = _make_fx_df()
        with self.assertRaises(AssetClassMismatch):
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=_FakeProfile())

    def test_rejects_equity_market_profile(self):
        from bot.quant_core.market_profile import AssetClassMismatch

        class _FakeProfile:
            asset_class = "EQUITY"

        df = _make_fx_df()
        with self.assertRaises(AssetClassMismatch):
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=_FakeProfile())

    def test_accepts_forex_market_profile(self):
        """No exception when profile.asset_class == 'FOREX'."""
        from bot.quant_core.market_profile import FOREX_PROFILE
        df = _make_fx_df()
        # Should complete without AssetClassMismatch (may raise other errors
        # if CSVs are missing, but not AssetClassMismatch)
        try:
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=FOREX_PROFILE)
        except Exception as exc:
            self.assertNotIsInstance(
                exc,
                __import__("bot.quant_core.market_profile", fromlist=["AssetClassMismatch"]).AssetClassMismatch,
                msg=f"AssetClassMismatch raised unexpectedly: {exc}",
            )


class SimulateFxPathInputValidationTests(TestCase):  # claude code changed: new — input validation (F1)
    def _forex_profile(self):
        from bot.quant_core.market_profile import FOREX_PROFILE
        return FOREX_PROFILE

    def test_missing_timestamp_column_raises(self):
        df = _make_fx_df()
        df = df.drop(columns=["timestamp"])
        with self.assertRaises(ValueError) as cm:
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=self._forex_profile())
        self.assertIn("timestamp", str(cm.exception).lower())

    def test_missing_ohlc_column_raises(self):
        df = _make_fx_df()
        df = df.drop(columns=["open"])
        with self.assertRaises(ValueError):
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=self._forex_profile())

    def test_duplicate_timestamps_raise(self):
        df = _make_fx_df(periods=200)
        df_duped = pd.concat([df, df.iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError) as cm:
            simulate_dca_path_fx(df_duped, FX_BASELINE_CONFIG, market_profile=self._forex_profile())
        self.assertIn("duplicate", str(cm.exception).lower())

    def test_ohlc_violation_raises(self):
        df = _make_fx_df(periods=200)
        df = df.copy()
        df.loc[10, "high"] = df.loc[10, "low"] - 0.001  # high < low
        with self.assertRaises(ValueError):
            simulate_dca_path_fx(df, FX_BASELINE_CONFIG, market_profile=self._forex_profile())


class SimulateFxPathOutputTests(TestCase):  # claude code changed: new — output shape and provenance (F1/F3)
    def setUp(self):
        from bot.quant_core.market_profile import FOREX_PROFILE
        self._profile = FOREX_PROFILE
        self._df = _make_fx_df(start="2021-01-04", periods=800)  # well before holdout

    def _run(self):
        return simulate_dca_path_fx(self._df, FX_BASELINE_CONFIG, market_profile=self._profile)

    def test_returns_two_tuple(self):
        result = self._run()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_mtm_frame_is_dataframe(self):
        _, mtm = self._run()
        self.assertIsInstance(mtm, pd.DataFrame)

    def test_trades_is_list(self):
        trades, _ = self._run()
        self.assertIsInstance(trades, list)

    def test_trades_carry_fx_cost_label(self):
        """Every closed trade must carry fx_cost_label (F3 — provenance)."""
        trades, _ = self._run()
        for t in trades:
            self.assertIn("fx_cost_label", t, msg=f"trade missing fx_cost_label: {list(t.keys())}")

    def test_trades_carry_cost_incomplete_label_when_costs_none(self):
        """FX_BASELINE_CONFIG has no cost specs — label must be COST_INCOMPLETE."""
        trades, _ = self._run()
        for t in trades:
            self.assertEqual(t["fx_cost_label"], "COST_INCOMPLETE")

    def test_trades_carry_fx_cost_complete_false(self):
        trades, _ = self._run()
        for t in trades:
            self.assertFalse(t["fx_cost_complete"])

    def test_accepts_weekend_gaps(self):
        """
        FX data with Mon-Fri gaps must run without assert_continuous_market error.
        This is the F1 core invariant: FX path does NOT call that check.
        """
        df_gaps = _make_fx_df_with_weekend_gaps(periods=500)
        # This must not raise AssetClassMismatch or any continuous-market error
        trades, mtm = simulate_dca_path_fx(df_gaps, FX_BASELINE_CONFIG, market_profile=self._profile)
        self.assertIsInstance(trades, list)
        self.assertIsInstance(mtm, pd.DataFrame)

    def test_cost_complete_label_when_all_costs_provided(self):
        """When all cost fields are provided, label must be COST_COMPLETE."""
        cfg = FxDcaConfig(
            levels=BASELINE_LEVELS,
            spread_pips=1.0,
            commission_per_lot=0.0,
            swap_long_pips=-3.5,
            swap_short_pips=1.2,
        )
        # pair_spec with is_cost_complete() True
        class _FakeSpec:
            def is_cost_complete(self):
                return True

        trades, _ = simulate_dca_path_fx(
            self._df, cfg, pair_spec=_FakeSpec(), market_profile=self._profile
        )
        for t in trades:
            self.assertEqual(t["fx_cost_label"], "COST_COMPLETE")
            self.assertTrue(t["fx_cost_complete"])
