# ============================================================
# bot/tests/test_dca_engine.py
# claude code changed: new file — DCA/Martingale research engine,
# 2026-09-26. Every case below is deterministic (synthetic price series
# with known, hand-computed outcomes), matching this project's own
# "no mocking, exact expected values" convention for pure-function logic
# (see bot/tests/test_pairs_health.py). Proves Phase 24's explicit ask —
# the $6/$762 baseline math is programmatically verified, not manually
# calculated and trusted.
# ============================================================

import pandas as pd
from django.test import SimpleTestCase

from bot.research.dca_engine import (
    BASELINE_CONFIG,
    BASELINE_LEVELS,
    DCAConfig,
    DCALevel,
    compute_theoretical_ladder,
    make_run_strategy_fn,
    simulate_dca_cycles,
    summarize_dca_cycles,
)


def _candles(prices, start="2024-01-01", freq="1h"):
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame({"close": prices}, index=idx)


class TheoreticalLadderMathTest(SimpleTestCase):
    """Phase 24: the observed $6/$762 baseline, verified programmatically."""

    def test_total_deployment_is_762(self):
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertEqual(result["total_deployment"], 762.0)

    def test_investment_ladder_matches_screenshots(self):
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertEqual(result["investments"], [6.0, 12.0, 24.0, 48.0, 96.0, 192.0, 384.0])

    def test_multipliers_match_screenshots(self):
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertEqual(result["max_level_multiplier"], 64.0)   # $384 / $6
        self.assertEqual(result["total_deployment_multiplier"], 127.0)  # $762 / $6

    def test_weighted_average_entry_matches_hand_calculation(self):
        # Real cost basis = total dollars spent / total units owned
        # (matches _OpenCycle.weighted_avg_price exactly — see this
        # function's own docstring for the real bug this replaced: a
        # dollar-weighted AVERAGE OF PRICES is a different, incorrect
        # statistic that overstates the true breakeven price).
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertAlmostEqual(result["weighted_avg_entry_pct"], 57.5057, places=3)

    def test_bottom_price_matches_hand_calculation(self):
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertAlmostEqual(result["bottom_price_pct"], 51.7046, places=3)

    def test_recovery_from_bottom_is_much_smaller_than_from_original_entry(self):
        # The entire appeal of the ladder: recovering from the bottom to
        # the profit target is far cheaper than recovering to the
        # original entry price.
        result = compute_theoretical_ladder(BASELINE_CONFIG)
        self.assertAlmostEqual(result["recovery_needed_from_bottom_pct"], 14.0, places=0)
        self.assertAlmostEqual(result["recovery_needed_from_original_entry_pct"], 93.4, places=0)
        self.assertLess(
            result["recovery_needed_from_bottom_pct"],
            result["recovery_needed_from_original_entry_pct"],
        )

    def test_custom_config_scales_correctly(self):
        # A completely different ladder shape must still compute
        # correctly — proves the math isn't hardcoded to the baseline.
        config = DCAConfig(initial_usdt=10.0, levels=(DCALevel(0.10, 1.0), DCALevel(0.10, 1.0)))
        result = compute_theoretical_ladder(config)
        self.assertEqual(result["total_deployment"], 30.0)  # 10 + 10 + 10
        self.assertEqual(result["investments"], [10.0, 10.0, 10.0])

    def test_theoretical_cost_basis_matches_simulator_on_identical_fills(self):
        # The regression test for the bug itself: theoretical and
        # simulated cost basis must agree exactly on the same fill
        # sequence, or one of the two formulas is wrong again.
        config = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS, fee_rate=0.0, slippage_rate=0.0)
        theoretical = compute_theoretical_ladder(config)
        toy_investments = theoretical["investments"]
        toy_prices = theoretical["price_path_pct"]
        total_qty = sum(inv / p for inv, p in zip(toy_investments, toy_prices))
        direct_cost_basis = sum(toy_investments) / total_qty
        self.assertAlmostEqual(theoretical["weighted_avg_entry_pct"], direct_cost_basis, places=4)


class WeightedAverageSimulationTest(SimpleTestCase):
    """Proves the candle-by-candle simulator reproduces the same
    weighted-average math as the pure theoretical function, when fed a
    price path that exactly matches the theoretical trigger sequence
    (zero fees/slippage isolates the pure averaging math)."""

    def test_full_ladder_fill_matches_theoretical_weighted_average(self):
        config = DCAConfig(
            initial_usdt=6.0, levels=BASELINE_LEVELS,
            profit_activation_pct=1.0,  # never triggers — isolates the fill/averaging logic
            fee_rate=0.0, slippage_rate=0.0,
        )
        # Step down slightly PAST each level's own trigger (drop_pct plus
        # a small epsilon), not exactly on the theoretical boundary — a
        # real price series never lands exactly on a floating-point
        # threshold, and testing at the exact boundary makes the outcome
        # depend on float rounding direction rather than on the >= logic
        # actually being correct. The theoretical reference below is
        # computed from these SAME nudged drops (not the clean baseline
        # figures), so the comparison stays exact.
        nudged_levels = tuple(DCALevel(lvl.drop_pct + 0.0005, lvl.multiplier) for lvl in BASELINE_LEVELS)
        nudged_config = DCAConfig(initial_usdt=6.0, levels=nudged_levels, fee_rate=0.0, slippage_rate=0.0)
        theoretical = compute_theoretical_ladder(nudged_config)

        prices = list(theoretical["price_path_pct"]) + [theoretical["price_path_pct"][-1]] * 5
        df = _candles(prices)

        trades = simulate_dca_cycles(df, config)   # simulate with the ORIGINAL (un-nudged) trigger levels
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["cycle_max_level_reached"], 6)
        self.assertEqual(trade["cycle_max_capital_deployed"], 762.0)
        # entry_price on the closed trade dict IS the weighted average —
        # compare against the pure-math reference, not a second hand calc.
        self.assertAlmostEqual(trade["entry_price"], theoretical["weighted_avg_entry_pct"], places=1)


class ExitLogicTest(SimpleTestCase):
    """Profit activation + trailing exit, in isolation."""

    def test_exits_after_activation_and_trail_breach(self):
        config = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.025, trailing_pct=0.005,
                            fee_rate=0.0, slippage_rate=0.0)
        # Entry at 100. Rises to 103 (activates, +3% > 2.5%), peaks at 105,
        # then falls to 104.4 (0.5% below the 105 peak) -> should exit.
        prices = [100, 101, 103, 105, 104.4, 90, 90]
        df = _candles(prices)
        trades = simulate_dca_cycles(df, config)

        self.assertGreaterEqual(len(trades), 1)
        first = trades[0]
        self.assertEqual(first["cycle_exit_reason"], "PROFIT_TARGET_TRAILING")
        self.assertAlmostEqual(first["exit_price"], 104.4, places=4)
        self.assertGreater(first["profit"], 0)

    def test_never_activates_without_reaching_threshold(self):
        config = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.025, trailing_pct=0.005,
                            fee_rate=0.0, slippage_rate=0.0)
        # Rises to only +1% — never activates — cycle stays open to data end.
        prices = [100, 100.5, 101, 100.8, 100.9]
        df = _candles(prices)
        trades = simulate_dca_cycles(df, config)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["cycle_exit_reason"], "DATA_END_STILL_OPEN")


class UnresolvedCyclesAreNeverDroppedTest(SimpleTestCase):
    """Phase 26 ('no false confidence'): a cycle still open when data ends
    must be reported, not silently excluded."""

    def test_still_open_cycle_appears_in_output(self):
        prices = [100, 99, 98, 97]  # drifts down, never exits, never DCAs (no levels configured)
        config = DCAConfig(initial_usdt=6.0, levels=(), fee_rate=0.0, slippage_rate=0.0)
        df = _candles(prices)
        trades = simulate_dca_cycles(df, config)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["cycle_exit_reason"], "DATA_END_STILL_OPEN")

    def test_summary_counts_unresolved_separately_from_resolved(self):
        prices = [100, 99, 98, 97]
        config = DCAConfig(initial_usdt=6.0, levels=(), fee_rate=0.0, slippage_rate=0.0)
        trades = simulate_dca_cycles(_candles(prices), config)
        summary = summarize_dca_cycles(trades)

        self.assertEqual(summary["n_unresolved_cycles"], 1)
        self.assertEqual(summary["n_resolved_cycles"], 0)
        self.assertIsNone(summary["cycle_win_rate_pct_of_resolved"])   # not fabricated as 0% or 100%


class CatastrophicCrashTest(SimpleTestCase):
    """Proves the simulator is NOT biased toward always looking safe — a
    relentless, bounce-free crash must produce a real, large loss with
    full ladder exhaustion, not a hidden/muted one."""

    def test_relentless_crash_exhausts_ladder_and_shows_real_loss(self):
        # Monotonic 80% decline over 200 candles, no bounces at all.
        prices = [100 * (0.99 ** i) for i in range(200)]  # ~86% decline
        df = _candles(prices)
        trades = simulate_dca_cycles(df, BASELINE_CONFIG)

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["cycle_exit_reason"], "DATA_END_STILL_OPEN")
        self.assertEqual(trade["cycle_max_level_reached"], 6)   # fully exhausted
        self.assertEqual(trade["cycle_max_capital_deployed"], 762.0)
        self.assertLess(trade["profit"], -300)   # a real, large loss — not a rounding artifact

    def test_max_capital_deployed_never_exceeds_theoretical_max(self):
        # However adversarial the price path, deployment is capped by the
        # ladder's own level count — this must hold structurally, not by luck.
        prices = [100 * (0.98 ** i) for i in range(500)]
        trades = simulate_dca_cycles(_candles(prices), BASELINE_CONFIG)
        theoretical_max = compute_theoretical_ladder(BASELINE_CONFIG)["total_deployment"]
        for t in trades:
            self.assertLessEqual(t["cycle_max_capital_deployed"], theoretical_max)


class FeesAndSlippageTest(SimpleTestCase):
    """Fees/slippage must actually reduce net profit relative to a
    zero-cost run on the identical price path — proves the cost model is
    wired in, not decorative."""

    def test_costed_run_has_lower_profit_than_zero_cost_run(self):
        prices = [100, 101, 103, 105, 104.4, 104, 103.9]
        zero_cost_config = DCAConfig(initial_usdt=6.0, levels=(), fee_rate=0.0, slippage_rate=0.0)
        real_cost_config = DCAConfig(initial_usdt=6.0, levels=(), fee_rate=0.001, slippage_rate=0.0005)

        zero_cost_trades = simulate_dca_cycles(_candles(prices), zero_cost_config)
        real_cost_trades = simulate_dca_cycles(_candles(prices), real_cost_config)

        self.assertGreater(zero_cost_trades[0]["profit"], real_cost_trades[0]["profit"])
        self.assertGreater(real_cost_trades[0]["cycle_fees_total"], 0)


class RunStrategyFnContractTest(SimpleTestCase):
    """The wrapper must conform exactly to what
    oos_validator.evaluate_strategy_oos() requires: every returned trade
    dict has non-None entry_time/exit_time (that function raises
    otherwise — see its own docstring)."""

    def test_every_trade_has_entry_and_exit_time(self):
        run_fn = make_run_strategy_fn(BASELINE_CONFIG)
        prices = [100, 95, 88, 105, 106, 105.4]
        trades = run_fn(_candles(prices), {})
        for t in trades:
            self.assertIsNotNone(t["entry_time"])
            self.assertIsNotNone(t["exit_time"])

    def test_ignores_fitted_params_without_error(self):
        run_fn = make_run_strategy_fn(BASELINE_CONFIG)
        prices = [100, 101, 103, 105, 104.4]
        # Passing an arbitrary, unrelated fitted_params dict must not break anything —
        # this baseline has no data-fitted parameters (matches MeanReversionStrategy's shape).
        trades = run_fn(_candles(prices), {"unrelated_key": 123})
        self.assertIsInstance(trades, list)


class ShortDirectionTest(SimpleTestCase):
    """SHORT is the mirror image of every LONG test above: DCA triggers
    on a RISE (not a drop), profit activates on a FALL below the
    weighted-average cost basis (not a rise above it), and the trailing
    exit tracks a trough (not a peak). Needed for FX, which is inherently
    two-sided — see DCAConfig.direction's own docstring."""

    def test_invalid_direction_raises(self):
        with self.assertRaises(ValueError):
            DCAConfig(direction="SIDEWAYS")

    def test_dca_triggers_on_price_rise_not_drop(self):
        config = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(DCALevel(0.05, 2.0),),
                            profit_activation_pct=1.0, fee_rate=0.0, slippage_rate=0.0)
        # Price DROPS 10% — must NOT trigger a SHORT's DCA level (that's the LONG condition).
        trades = simulate_dca_cycles(_candles([100, 90, 90, 90, 90]), config)
        self.assertEqual(trades[0]["cycle_n_fills"], 1)   # never DCA'd

        # Price RISES 6% (past the 5% level) — must trigger.
        trades = simulate_dca_cycles(_candles([100, 106, 106, 106, 106]), config)
        self.assertEqual(trades[0]["cycle_n_fills"], 2)

    def test_short_profits_when_price_falls_below_cost_basis(self):
        config = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(),
                            profit_activation_pct=0.025, trailing_pct=0.005, fee_rate=0.0, slippage_rate=0.0)
        # Entry at 100 (short). Falls to 97 (+3% short profit > 2.5% activation),
        # troughs at 96, bounces to 96.5 (0.5% above the 96 trough) -> exit.
        prices = [100, 99, 97, 96, 96.5, 105, 105]
        trades = simulate_dca_cycles(_candles(prices), config)
        first = trades[0]
        self.assertEqual(first["cycle_exit_reason"], "PROFIT_TARGET_TRAILING")
        self.assertGreater(first["profit"], 0)   # short profited from the price FALL
        self.assertAlmostEqual(first["exit_price"], 96.5, places=4)

    def test_short_loses_when_forced_to_close_after_price_rises(self):
        # Mirror of a LONG loss: a SHORT that never gets its favorable
        # (falling) move and is forced closed at a higher price than entry
        # must show a real, negative profit — not silently clipped to zero.
        config = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(), max_cycle_candles=3,
                            profit_activation_pct=0.025, trailing_pct=0.005, fee_rate=0.0, slippage_rate=0.0)
        prices = [100, 105, 110, 115, 115]
        trades = simulate_dca_cycles(_candles(prices), config)
        self.assertEqual(trades[0]["cycle_exit_reason"], "MAX_CYCLE_CANDLES_TIMEOUT")
        self.assertLess(trades[0]["profit"], 0)

    def test_short_weighted_average_rises_as_ladder_fills_on_rising_price(self):
        # For SHORT, DCA fills happen at PROGRESSIVELY HIGHER prices (the
        # adverse direction), so the weighted-average cost basis must rise
        # above the initial entry price — the mirror of LONG's basis
        # falling below its initial entry.
        config = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=BASELINE_LEVELS,
                            profit_activation_pct=1.0, fee_rate=0.0, slippage_rate=0.0)
        prices = [100, 105.1, 112.5, 123.9, 142.5, 163.9, 182.2] + [182.2] * 5
        trades = simulate_dca_cycles(_candles(prices), config)
        self.assertEqual(trades[0]["cycle_max_level_reached"], 6)
        self.assertGreater(trades[0]["entry_price"], 100.0)   # weighted-avg basis rose, as expected for SHORT

    def test_short_and_long_are_symmetric_on_mirrored_price_paths(self):
        # A LONG on a falling-then-rising path and a SHORT on the
        # mirrored (rising-then-falling) path should produce the same
        # magnitude of profit — proves the two directions are true
        # mirror images, not independently-drifted implementations.
        long_config = DCAConfig(initial_usdt=6.0, direction="LONG", levels=(),
                                 profit_activation_pct=0.025, trailing_pct=0.005, fee_rate=0.0, slippage_rate=0.0)
        short_config = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(),
                                  profit_activation_pct=0.025, trailing_pct=0.005, fee_rate=0.0, slippage_rate=0.0)

        long_prices = [100, 99, 97, 96, 96.5, 105, 105]                    # falls then rises
        short_prices = [100, 101, 103, 104, 103.5, 95, 95]                 # mirrored: rises then falls by the same magnitude

        long_trades = simulate_dca_cycles(_candles(long_prices), long_config)
        short_trades = simulate_dca_cycles(_candles(short_prices), short_config)

        self.assertAlmostEqual(long_trades[0]["profit"], short_trades[0]["profit"], places=4)
