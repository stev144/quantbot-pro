# claude code changed: new file — 2026-10-03, owner decision D-5 (DCA significance redesign; power-gated).
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import dca_significance_d5 as d5


class StatisticTests(SimpleTestCase):
    def test_single_statistic_is_the_engines_mtm_per_max_deployed(self):
        from bot.research.dca_engine import BASELINE_CONFIG, _mtm_statistics
        df = d5.planted_mean_reversion_path(3000, 0.05, 72, np.random.default_rng(0))
        self.assertEqual(d5.d5_statistic(df, BASELINE_CONFIG), float(_mtm_statistics(df, BASELINE_CONFIG)[1]))


class PlantedEffectTests(SimpleTestCase):
    def test_ou_component_has_the_declared_half_life_and_stationary_sd(self):
        rng = np.random.default_rng(1)
        n, amp, hl = 200_000, 0.05, 72
        df = d5.planted_mean_reversion_path(n, amp, hl, rng, rw_sigma=0.0)          # isolate the OU component
        x = np.log(df["close"].to_numpy()) - np.log(100.0)
        phi_hat = np.corrcoef(x[:-1], x[1:])[0, 1]
        self.assertAlmostEqual(phi_hat, np.exp(-np.log(2) / hl), delta=0.002)
        self.assertAlmostEqual(x.std(), amp, delta=0.005)

    def test_zero_amplitude_is_a_pure_random_walk_null(self):
        df = d5.planted_mean_reversion_path(1000, 0.0, 24, np.random.default_rng(2), rw_sigma=0.01)
        r = np.diff(np.log(df["close"].to_numpy()))
        self.assertLess(abs(np.corrcoef(r[:-1], r[1:])[0, 1]), 0.1)

    def test_only_the_declared_amplitude_definition_is_accepted(self):
        with self.assertRaises(ValueError):
            d5.planted_mean_reversion_path(100, 0.05, 24, np.random.default_rng(0), amplitude_definition="peak")


class PowerGateTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.art = self.d / "cal.json"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, power, version=d5.D5_VERSION):
        self.art.write_text(json.dumps({"version": version, "power": power}))

    def test_market_test_refused_until_80pct_power_at_the_smallest_effect_for_every_half_life(self):
        with self.assertRaises(d5.DCANotCalibrated):
            d5.require_power(self.art)
        ok = {f"amp0.05_hl{h}": 0.85 for h in d5.HALF_LIVES_H}
        self._write(dict(ok, **{"amp0.05_hl168": 0.79}))
        with self.assertRaisesRegex(d5.DCANotCalibrated, "DESCRIPTIVE ONLY"):
            d5.require_power(self.art)
        self._write({k: v for k, v in ok.items() if k != "amp0.05_hl24"})
        with self.assertRaises(d5.DCANotCalibrated):
            d5.require_power(self.art)
        self._write(ok, version="OLD")
        with self.assertRaises(d5.DCANotCalibrated):
            d5.require_power(self.art)
        self._write(ok)
        self.assertEqual(d5.require_power(self.art)["version"], d5.D5_VERSION)

    def test_calibration_requires_the_open_owner_definitions(self):
        with self.assertRaisesRegex(ValueError, "owner definitions"):
            d5.run_d5_calibration(mean_block=None, benchmark=None, config=None, reps=1, n_boot=1, n_candles=10, seed=0)
