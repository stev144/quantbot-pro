# ============================================================
# bot/tests/test_pairs_config.py
# claude code changed: new file — pairs pilot 10-pair expansion,
# 2026-09-28. Covers bot/pairs/config.py's real, current state: loads
# the actual research_data/cointegration_pairs.csv and
# research_data/permutation_test/ files on disk (no mocking, matching
# this project's convention — a mocked CSV can't catch a real stale-data
# regression the way reading the real file can), so these tests are
# deliberately coupled to the real research data, not hardcoded fixtures.
# ============================================================

from django.test import SimpleTestCase

from bot.pairs.config import (
    PAIR_NAMES,
    ENTRY_ELIGIBLE_PAIR_NAMES,
    TOTAL_PILOT_CAPITAL_USDT,
    PER_PAIR_CAPITAL_USDT,
    load_pair_configs,
)


class PairNamesShapeTest(SimpleTestCase):
    def test_ten_pairs_configured(self):
        self.assertEqual(len(PAIR_NAMES), 10)

    def test_entry_eligible_is_a_subset_of_all_pairs(self):
        self.assertTrue(set(ENTRY_ELIGIBLE_PAIR_NAMES).issubset(set(PAIR_NAMES)))

    def test_at_least_one_pair_is_entry_eligible(self):
        # claude code changed: not a tautology — this fails loudly if a
        # future cointegration re-scan ever fails EVERY pair, which would
        # otherwise silently zero PER_PAIR_CAPITAL_USDT's denominator
        # (guarded, see below) and leave the whole pilot with no
        # tradeable pair without anyone noticing.
        self.assertGreater(len(ENTRY_ELIGIBLE_PAIR_NAMES), 0)


class LoadPairConfigsTest(SimpleTestCase):
    def setUp(self):
        self.configs = load_pair_configs()
        self.by_name = {c.pair_name: c for c in self.configs}

    def test_loads_all_ten_pairs(self):
        self.assertEqual(len(self.configs), 10)
        self.assertEqual(set(self.by_name.keys()), set(PAIR_NAMES))

    def test_entry_eligible_pairs_have_real_capital_and_win_rate(self):
        for name in ENTRY_ELIGIBLE_PAIR_NAMES:
            cfg = self.by_name[name]
            self.assertTrue(cfg.is_entry_eligible)
            self.assertTrue(cfg.passes_filters)
            self.assertIsNotNone(cfg.validated_win_rate)
            self.assertGreater(cfg.validated_win_rate, 0.0)
            self.assertAlmostEqual(cfg.capital_usdt, PER_PAIR_CAPITAL_USDT)

    def test_non_eligible_pairs_get_zero_capital_and_no_win_rate(self):
        non_eligible = set(PAIR_NAMES) - set(ENTRY_ELIGIBLE_PAIR_NAMES)
        self.assertGreater(len(non_eligible), 0)  # this expansion's whole premise — see module docstring in config.py
        for name in non_eligible:
            cfg = self.by_name[name]
            self.assertFalse(cfg.is_entry_eligible)
            self.assertEqual(cfg.capital_usdt, 0.0)
            self.assertIsNone(cfg.validated_win_rate)

    def test_every_pair_has_a_real_seed_hedge_ratio(self):
        # claude code changed: hedge_ratio/intercept come from the OLS fit,
        # which happens for every pair BEFORE the cointegration filter is
        # applied — so even a monitoring-only pair must have a real,
        # non-placeholder seed beta to track (PairMonitorEngine needs it).
        for cfg in self.configs:
            self.assertIsInstance(cfg.hedge_ratio, float)
            self.assertNotEqual(cfg.hedge_ratio, 0.0)

    def test_capital_allocation_sums_to_total_pilot_capital(self):
        allocated = sum(c.capital_usdt for c in self.configs)
        self.assertAlmostEqual(allocated, TOTAL_PILOT_CAPITAL_USDT, places=2)

    def test_reject_reason_present_for_pairs_that_fail_filters(self):
        for cfg in self.configs:
            if not cfg.passes_filters:
                self.assertTrue(cfg.reject_reason, f"{cfg.pair_name} fails filters but has an empty reject_reason")
