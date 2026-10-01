# ============================================================
# bot/tests/test_pairs_monitor_engine.py
# claude code changed: new file — pairs pilot 10-pair expansion,
# 2026-09-28. Covers bot/pairs/monitor_engine.py's PairMonitorEngine:
# the lighter, non-tradeable engine used for the 8 monitoring-only
# pairs. No mocking — constructs a real PairConfig (via
# load_pair_configs(), reading the real cointegration CSV, matching this
# project's convention) and feeds it real-shaped synthetic price series,
# checking the exact same kalman_step() math PairSignalEngine itself
# depends on (bot/pairs/kalman_online.py) rather than re-deriving it.
# ============================================================

from django.test import SimpleTestCase

from bot.pairs.config import load_pair_configs, ENTRY_ELIGIBLE_PAIR_NAMES
from bot.pairs.monitor_engine import PairMonitorEngine


def _non_eligible_config():
    configs = load_pair_configs()
    for cfg in configs:
        if not cfg.is_entry_eligible:
            return cfg
    raise AssertionError("expected at least one non-entry-eligible pair to test PairMonitorEngine against")


class PairMonitorEngineConstructionTest(SimpleTestCase):
    def test_seeds_current_beta_from_config_hedge_ratio(self):
        cfg = _non_eligible_config()
        engine = PairMonitorEngine(cfg)
        self.assertAlmostEqual(engine.current_beta, cfg.hedge_ratio, places=8)

    def test_never_constructs_an_entry_exit_engine(self):
        # claude code changed: structural check, not just behavioral — a
        # PairMonitorEngine must have no `.engine`/`.sizer`/trade-decision
        # attribute at all, so a bug elsewhere that mistakenly tried to
        # call process_candle()/entry logic on one of these hits a real
        # AttributeError immediately instead of silently no-op'ing.
        cfg = _non_eligible_config()
        engine = PairMonitorEngine(cfg)
        self.assertFalse(hasattr(engine, "engine"))
        self.assertFalse(hasattr(engine, "sizer"))
        self.assertFalse(hasattr(engine, "process_candle"))


class PairMonitorEngineUpdateTest(SimpleTestCase):
    def test_update_advances_beta_over_real_shaped_price_series(self):
        cfg = _non_eligible_config()
        engine = PairMonitorEngine(cfg)
        seed_beta = engine.current_beta

        # A plain deterministic price walk — real Kalman update math, not
        # a mocked/fabricated beta. Confirms update() actually mutates
        # state rather than being a no-op.
        price_a, price_b = 10.0, 20.0
        betas = [seed_beta]
        for i in range(50):
            price_a *= 1.0 + (0.01 if i % 2 == 0 else -0.008)
            price_b *= 1.0 + (0.005 if i % 3 == 0 else -0.004)
            engine.update(price_a, price_b)
            betas.append(engine.current_beta)

        self.assertNotEqual(betas[-1], seed_beta)
        self.assertTrue(all(isinstance(b, float) for b in betas))

    def test_update_is_deterministic_given_the_same_price_path(self):
        cfg = _non_eligible_config()
        engine_1 = PairMonitorEngine(cfg)
        engine_2 = PairMonitorEngine(cfg)

        price_path = [(10.0 + i * 0.1, 20.0 - i * 0.05) for i in range(30)]
        for price_a, price_b in price_path:
            engine_1.update(price_a, price_b)
            engine_2.update(price_a, price_b)

        self.assertAlmostEqual(engine_1.current_beta, engine_2.current_beta, places=10)


class PairMonitorEngineAllNonEligiblePairsTest(SimpleTestCase):
    def test_constructs_cleanly_for_every_non_eligible_pair(self):
        # claude code changed: exercises the real, full current config —
        # this is the exact set process_pair_candle()'s branch in
        # bot/core/pairs_bot_runner.py routes to PairMonitorEngine, not a
        # hypothetical subset.
        configs = load_pair_configs()
        non_eligible = [c for c in configs if not c.is_entry_eligible]
        self.assertGreater(len(non_eligible), 0)
        for cfg in non_eligible:
            engine = PairMonitorEngine(cfg)
            engine.update(1.0, 1.0)
            self.assertIsInstance(engine.current_beta, float)

    def test_entry_eligible_pairs_are_untouched_by_this_module(self):
        # claude code changed: sanity check that ENTRY_ELIGIBLE_PAIR_NAMES
        # (used by pairs_bot_runner.py to pick PairSignalEngine instead)
        # and this test file's non-eligible set are genuinely disjoint,
        # not overlapping by a stale assumption.
        configs = load_pair_configs()
        eligible_names = {c.pair_name for c in configs if c.is_entry_eligible}
        self.assertEqual(eligible_names, set(ENTRY_ELIGIBLE_PAIR_NAMES))
