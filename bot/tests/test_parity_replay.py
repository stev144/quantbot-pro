# claude code changed: new file — Stage 2D Phase O. Research/live parity on
# identical synthetic candles (bot/research/parity_replay.py). No exchange.
#
# Passing tests assert parity that exists today. Tests marked expectedFailure
# encode a parity REQUIREMENT of Stage 2C §17 that the code does not meet yet;
# each names the open decision blocking it. When the divergence is fixed the
# test will start passing and expectedFailure turns it red — remove the
# decorator then.

import contextlib
import io
import logging
import math
import unittest

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.parity_replay import replay

H = pd.Timedelta(hours=1)


def _pair(n=1500, seed=21, hl=6.0):
    g = np.random.default_rng(seed)
    y = np.cumsum(g.normal(0, 0.01, n))
    phi = math.exp(-math.log(2) / hl)
    u, e = np.zeros(n), g.normal(0, 0.012, n)
    for t in range(1, n):
        u[t] = phi * u[t - 1] + e[t]
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.Series(np.exp(0.2 + 1.3 * y + u) * 10, index=idx), pd.Series(np.exp(y) * 10, index=idx)


def _replay(**kw):
    ca, cb = _pair()
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):   # the research engine prints its own report
            return replay(ca, cb, hedge_ratio=1.3, intercept=0.2 - 0.3 * math.log(10),
                          validated_win_rate=0.6, capital_usdt=1000.0, **kw)
    finally:
        logging.disable(logging.NOTSET)


class ParityReplayTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = _replay()

    # ── parity that holds today ──────────────────────────────────────────────
    def test_shared_kalman_mathematics_is_identical(self):
        for name in ("beta_prior", "innovation", "zscore"):
            f = self.report.fields[name]
            self.assertTrue(f.matches, f"{name}: max |diff| {f.max_abs_diff} over {f.n_compared}")
            self.assertGreater(f.n_compared, 1000)

    def test_measured_live_decision_lag_is_one_candle(self):
        """Measurement of the CURRENT live clock (decides at candle c on c−1's
        values), not an endorsement of it — see the expectedFailure below."""
        self.assertEqual(self.report.decision_lag_candles, 1)

    def test_every_research_entry_has_a_live_counterpart(self):
        live = {(t - H, d) for t, d in self.report.live_entries}
        self.assertTrue(self.report.research_entries)
        for entry in self.report.research_entries:
            self.assertIn(entry, live)

    def test_harness_detects_a_skipped_live_candle(self):
        r = _replay(live_skip=(800,))
        ca, _ = _pair()
        self.assertEqual(r.first_state_divergence, ca.index[801])   # first candle live processes after the skip
        self.assertFalse(r.fields["beta_prior"].matches)

    # ── Stage 2C §17 requirements not met yet ────────────────────────────────
    @unittest.expectedFailure
    def test_decision_clock_parity(self):
        """Required: research and live decide on the same cutoff. Blocked on
        OPEN-25 (live loop scheduling) / OPEN-1b (delay definition)."""
        self.assertTrue(self.report.fields["decision_zscore_same_candle"].matches)

    @unittest.expectedFailure
    def test_beta_uncertainty_parity(self):
        """Required: one uncertainty definition (posterior P[0,0]). Live uses the
        prior; changing it is a live-path change (OPEN-34 boundary decision)."""
        self.assertTrue(self.report.fields["beta_uncertainty"].matches)

    @unittest.expectedFailure
    def test_entry_and_exit_sequences_identical(self):
        """Required: identical trade sequences. Live exits on its decision candle
        and can re-enter one candle later; research exits fill at u+d (d ≥ 1)
        and partial exits exist only in research. Blocked on OPEN-6a/OPEN-29
        (exit architecture and live alignment) and OPEN-1c (production d)."""
        live = [(t - H, d) for t, d in self.report.live_entries]
        self.assertEqual(live, self.report.research_entries)
