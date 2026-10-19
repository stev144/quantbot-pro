# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_d4_speedup_equivalence.py
# claude code changed: new file — 2026-10-07 D-4 speed-up (owner: "proceed with the most recommended" = option C,
# bit-identical acceleration). Every optimisation must reproduce the original bytes exactly:
#   * data_version's column-wise fast path == the original per-row path (and falls back where it cannot prove it);
#   * measurement_update's broadcast products == np.outer, bit for bit;
#   * the cached Kalman Q equals np.diag(q) and cannot be mutated.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core import provenance as pv


def _slow(frames, **kw):
    with mock.patch.object(pv, "_iso_strings", lambda g: None):
        return pv.data_version(frames, **kw).data_version


class DataVersionFastPathTests(SimpleTestCase):

    def frames(self):
        rng = np.random.default_rng(4)
        idx = pd.date_range("2021-11-01", periods=500, freq="h", tz="UTC")
        a = pd.DataFrame({"close": rng.normal(100, 1, 500), "volume": rng.integers(0, 10**9, 500)}, index=idx)
        a.iloc[7, 0] = np.nan                                   # a NaN value inside a present row
        b = a.drop(idx[100:110]) * 1.5                          # missing candles → MISSING lines
        b["close"] = b["close"] + 1e-13                         # values whose repr needs all 17 digits
        return {"A": a, "B": b}

    def test_fast_path_is_byte_identical(self):
        f = self.frames()
        for cols in (["close"], ["close", "volume"]):
            with self.subTest(cols=cols):
                fast = pv.data_version(f, timeframe="1h", columns=cols)
                self.assertEqual(fast.data_version, _slow(f, timeframe="1h", columns=cols))
        self.assertIsNotNone(pv._iso_strings(pd.date_range("2021-11-01", periods=3, freq="h", tz="UTC")))

    def test_iso_strings_equal_isoformat_for_ns_and_us_storage(self):
        g = pd.date_range("2021-11-07", periods=1000, freq="h", tz="UTC")
        for grid in (g, g.as_unit("us"), g.as_unit("s")):
            self.assertEqual(pv._iso_strings(grid), [t.isoformat() for t in grid])

    def test_falls_back_where_identity_is_not_guaranteed(self):
        self.assertIsNone(pv._iso_strings(pd.date_range("2021-11-07 00:00:00.5", periods=3, freq="h", tz="UTC")))
        self.assertIsNone(pv._iso_strings(pd.date_range("2021-11-07", periods=3, freq="h", tz="Europe/London")))
        obj = pd.DataFrame({"close": [1.0, None]}, dtype=object)
        self.assertIsNone(pv._fast_value_texts(obj))
        idx = pd.date_range("2021-11-01", periods=4, freq="h", tz="UTC")
        o = {"A": pd.DataFrame({"close": pd.Series([1.0, 2.0, 3.0, 4.0], dtype=object)}, index=idx)}
        self.assertEqual(pv.data_version(o, timeframe="1h", columns=["close"]).data_version,
                         _slow(o, timeframe="1h", columns=["close"]))


class KalmanArithmeticTests(SimpleTestCase):

    def test_broadcast_products_equal_np_outer_bitwise(self):
        from bot.quant_core.kalman import _I2, measurement_update
        rng = np.random.default_rng(0)
        th, P, r = np.array([1.0, 0.1]), np.eye(2), 1e-3
        for _ in range(3000):
            la, lb = float(rng.normal() + 3), float(rng.normal() + 2)
            Pp = P + np.diag([1e-5, 1e-5])
            u = measurement_update(th, Pp, la, lb, r)
            H = np.array([lb, 1.0])                              # the pre-speed-up formulation, verbatim
            K = Pp @ H / float(H @ Pp @ H + r)
            KH = np.outer(K, H)
            ref = (_I2 - KH) @ Pp @ (_I2 - KH).T + np.outer(K, K) * r
            self.assertTrue(np.array_equal(u.P_post, ref) and np.array_equal(u.gain, K))
            th, P = u.theta_post, u.P_post

    def test_cached_q_is_exact_read_only_and_follows_the_protocol(self):
        from dataclasses import replace
        from bot.quant_core.kalman import CanonicalKalman
        from bot.quant_core.protocol import CANONICAL
        k = CanonicalKalman(np.array([1.0, 0.0]))
        self.assertTrue(np.array_equal(k.Q, np.diag([CANONICAL.q_beta, CANONICAL.q_alpha])))
        self.assertIs(k.Q, k.Q)
        with self.assertRaises(ValueError):
            k.Q[0, 0] = 1.0
        k.protocol = replace(CANONICAL, q_beta=2e-5)
        self.assertEqual(k.Q[0, 0], 2e-5)
