# claude code changed: new file — Stage 2D Rev B Steps 9–11 (decisions C1, C2,
# C3, C5; STAGE2D_DECISION_LOCK.md D1/D2). Known-answer tests of the canonical
# trade lifecycle in bot/quant_core/lifecycle.py, the research engine
# (EntryExitEngine rule_set="CANONICAL") and the live engine (PairSignalEngine).
#
# Expected timestamps/holding periods are derived here by plain arithmetic on
# the scenario's own construction (E = t + 1, decision E + 23, fill E + 24 …),
# never by calling the code under test. Prices are BUILT from a chosen held
# spread path: P_A = exp(h + α_e + β_e ln P_B), so the sign of h at every
# candle is known by construction.

import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.pairs.kalman_online import NativeStepResult
from bot.pairs.signal_engine import PairSignalEngine
from bot.quant_core import lifecycle as lc
from bot.quant_core.protocol import CANONICAL
from bot.research.entry_exit_engine import EntryExitEngine, PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT
from bot.research.parity_forensics import _cfg

BETA_E, ALPHA_E = 1.3, 0.2
H = 24
T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
HR = pd.Timedelta(hours=1)


def _pb(k):
    return 10.0 * (1.0 + 0.001 * k)            # B moves, so h is not trivially a function of P_A


def _pa(h, pb):
    return math.exp(h + ALPHA_E + BETA_E * math.log(pb))


class LifecycleUnitTests(SimpleTestCase):
    def test_held_spread_known_answer(self):
        # Decision-lock §1.6 example: P_B = 10, P_A = 23.649941 → h = −0.030000
        self.assertAlmostEqual(lc.held_spread(23.649941, 10.0, 1.3, 0.2), -0.030000, places=6)
        self.assertAlmostEqual(lc.held_spread(25.112372, 10.0, 1.3, 0.2), +0.030000, places=6)
        self.assertTrue(math.isnan(lc.held_spread(-1.0, 10.0, 1.3, 0.2)))

    def test_direction_and_sign_rules_are_symmetric(self):
        self.assertEqual(lc.entry_direction(-2.0), lc.LONG)          # |z| ≥ 2.0 inclusive
        self.assertEqual(lc.entry_direction(+2.0), lc.SHORT)
        self.assertIsNone(lc.entry_direction(1.999999))
        self.assertIsNone(lc.entry_direction(None))
        self.assertTrue(lc.entry_sign_valid(lc.LONG, -1e-9) and lc.entry_sign_valid(lc.SHORT, 1e-9))
        for d, h in ((lc.LONG, 0.0), (lc.LONG, 0.01), (lc.SHORT, 0.0), (lc.SHORT, -0.01), (lc.LONG, math.nan)):
            self.assertFalse(lc.entry_sign_valid(d, h), (d, h))

    def test_exit_rule_known_answers(self):
        self.assertIsNone(lc.exit_decision(lc.LONG, +0.5, 0))                # C2: never on the entry-fill close
        self.assertEqual(lc.exit_decision(lc.LONG, 0.0, 1), lc.NORMALISATION)  # h ≥ 0
        self.assertEqual(lc.exit_decision(lc.SHORT, 0.0, 1), lc.NORMALISATION) # h ≤ 0
        self.assertIsNone(lc.exit_decision(lc.LONG, -0.01, 22))
        self.assertEqual(lc.exit_decision(lc.LONG, -0.01, 23), lc.TIMESTOP)  # decision E + H − 1
        self.assertEqual(lc.exit_decision(lc.SHORT, +0.01, 23), lc.TIMESTOP)
        self.assertIsNone(lc.exit_decision(lc.SHORT, +0.01, 22))
        self.assertEqual(CANONICAL.time_stop_decision_offset(), 23)


class ResearchCanonicalScanTests(SimpleTestCase):
    """EntryExitEngine(rule_set="CANONICAL") on constructed candles."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *, z_at_t, h_at, n=60, t=10, drop=()):
        """h_at(k) → held spread at candle k; z = z_at_t at candle t, 0 elsewhere."""
        rows = []
        for k in range(n):
            if k in drop:
                continue
            pb = _pb(k)
            rows.append({"timestamp": T0 + k * HR, "kalman_zscore": z_at_t if k == t else 0.0,
                         "kalman_zscore_lag1": 0.0, "pair_signal_dynamic": -(z_at_t if k == t else 0.0),
                         "kalman_beta": BETA_E, "kalman_alpha": ALPHA_E, "kalman_spread": 0.0,
                         "prediction_error": 0.0, "beta_uncertainty": 0.0,
                         "close_a": _pa(h_at(k), pb), "close_b": pb})
        csv = self.tmp / "X_USDT_Y_USDT_kalman.csv"
        pd.DataFrame(rows).to_csv(csv, index=False)
        eng = EntryExitEngine(output_dir=str(self.tmp / "out"), validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                              rule_set="CANONICAL_UNCERTIFIED", capital_usdt=10_000.0,   # claude code changed: Stage 2D Rev B D-4 — hand-built input has no canonical provenance
                              pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT")
        trades, summary, _ = eng.run(kalman_csv=str(csv))
        return eng, trades, summary

    def test_valid_long_opens_and_short_symmetric(self):
        for z, h0, direction in ((-2.5, -0.03, lc.LONG), (+2.5, +0.03, lc.SHORT)):
            with self.subTest(direction=direction):
                eng, trades, _ = self._run(z_at_t=z, h_at=lambda k, h0=h0: h0)   # h never crosses → time stop
                self.assertEqual(len(trades), 1)
                tr = trades.iloc[0]
                self.assertEqual(tr["direction"], direction)
                self.assertEqual(pd.Timestamp(tr["entry_decision_timestamp"]), T0 + 10 * HR)
                self.assertEqual(pd.Timestamp(tr["entry_timestamp"]), T0 + 11 * HR)        # E = t + 1
                self.assertAlmostEqual(tr["entry_held_spread"], h0, places=12)
                self.assertEqual(eng.entry_sign_mismatches, [])

    def test_mismatched_long_and_short_are_rejected_recorded_and_inert(self):
        for z, h0, direction in ((-2.5, +0.03, lc.LONG), (+2.5, -0.03, lc.SHORT)):
            with self.subTest(direction=direction):
                eng, trades, summary = self._run(z_at_t=z, h_at=lambda k, h0=h0: h0)
                self.assertTrue(trades.empty)                       # no trade → no P&L, no statistics
                self.assertTrue(summary.empty)
                self.assertEqual(eng.completed_trades, [])
                log = eng.entry_sign_mismatch_log()
                self.assertEqual(len(log), 1)
                m = log.iloc[0]
                self.assertEqual(m["reason"], lc.ENTRY_SIGN_MISMATCH)
                self.assertEqual(m["intended_direction"], direction)
                self.assertEqual(pd.Timestamp(m["decision_timestamp"]), T0 + 10 * HR)
                self.assertAlmostEqual(m["held_spread"], h0, places=12)
                self.assertEqual((m["alpha_e"], m["beta_e"], m["zscore"]), (ALPHA_E, BETA_E, z))
                self.assertAlmostEqual(m["price_b"], _pb(10), places=12)
                saved = pd.read_csv(self.tmp / "out" / "X_Y_entry_sign_mismatches.csv")
                self.assertEqual(len(saved), 1)

    def test_no_same_close_exit_and_first_exit_decision_is_e_plus_1(self):
        # LONG; h ≥ 0 already AT the entry-fill candle E = 11 (and after).
        eng, trades, _ = self._run(z_at_t=-2.5, h_at=lambda k: -0.03 if k <= 10 else +0.01)
        tr = trades.iloc[0]
        self.assertEqual(pd.Timestamp(tr["exit_decision_timestamp"]), T0 + 12 * HR)   # E + 1, not E
        self.assertEqual(pd.Timestamp(tr["exit_timestamp"]), T0 + 13 * HR)            # fill E + 2
        self.assertEqual(tr["exit_reason"], lc.NORMALISATION)
        self.assertEqual(tr["holding_candles"], 2)

    def test_time_stop_holds_exactly_h(self):
        eng, trades, _ = self._run(z_at_t=+2.5, h_at=lambda k: +0.03)   # SHORT, never normalises
        tr = trades.iloc[0]
        E = T0 + 11 * HR
        self.assertEqual(pd.Timestamp(tr["exit_decision_timestamp"]), E + (H - 1) * HR)
        self.assertEqual(pd.Timestamp(tr["exit_timestamp"]), E + H * HR)
        self.assertEqual((tr["exit_reason"], tr["holding_candles"], tr["h_compliant"], tr["data_complete"]),
                         (lc.TIMESTOP, H, True, True))

    def test_missing_exit_fill_candle_is_flagged_not_disguised(self):
        E = 11
        eng, trades, summary = self._run(z_at_t=-2.5, h_at=lambda k: -0.03, drop=(E + H,))
        tr = trades.iloc[0]
        self.assertEqual(pd.Timestamp(tr["exit_decision_timestamp"]), T0 + (E + H - 1) * HR)
        self.assertEqual(pd.Timestamp(tr["exit_timestamp"]), T0 + (E + H + 1) * HR)     # first observed after u + d
        self.assertEqual(tr["holding_candles"], H + 1)
        self.assertTrue(tr["exit_fill_candle_missing"])
        self.assertEqual(tr["missing_exit_fill_timestamps"], (T0 + (E + H) * HR).isoformat())
        self.assertFalse(tr["h_compliant"])
        self.assertFalse(tr["data_complete"])
        self.assertEqual(int(summary.iloc[0]["trades_exit_fill_missing"]), 1)
        self.assertEqual(int(summary.iloc[0]["trades_data_incomplete"]), 1)

    def test_data_end_censoring_is_marked(self):
        eng, trades, _ = self._run(z_at_t=-2.5, h_at=lambda k: -0.03, n=20)   # data ends before E + H
        tr = trades.iloc[0]
        self.assertEqual(tr["exit_reason"], "DATA_END_CENSORED")
        self.assertFalse(tr["data_complete"])
        self.assertFalse(tr["h_compliant"])

    def test_missing_entry_fill_candle_skips(self):
        eng, trades, _ = self._run(z_at_t=-2.5, h_at=lambda k: -0.03, drop=(11,))
        self.assertTrue(trades.empty)
        self.assertEqual(eng.n_entries_skipped_missing_fill, 1)

    def test_canonical_rejects_legacy_only_configurations(self):
        with self.assertRaises(ValueError):
            EntryExitEngine(output_dir=str(self.tmp), validated_win_rate=0.6, rule_set="CANONICAL", resample_hours=4)
        with self.assertRaises(ValueError):
            EntryExitEngine(output_dir=str(self.tmp), validated_win_rate=0.6, rule_set="CANONICAL", signal_source="ols")
        with self.assertRaises(ValueError):
            EntryExitEngine(output_dir=str(self.tmp), validated_win_rate=0.6, rule_set="SOMETHING")


class LiveCanonicalLifecycleTests(SimpleTestCase):
    """PairSignalEngine. The entry decision bundle is injected (white-box) so
    the scenario controls z and θ(t|t) exactly; everything after the entry —
    exit decisions, fills, holding — runs through the real code."""

    def _engine_at_decision(self, z, h_t):
        eng = PairSignalEngine(_cfg(BETA_E, ALPHA_E, 0.6, 10_000.0))
        t = T0 + 10 * HR
        pb = _pb(10)
        pa = _pa(h_t, pb)
        eng._last_ts, eng._last_observed = t, (t, pa, pb)
        eng._prev_native = NativeStepResult(
            beta_pred=BETA_E, alpha_pred=ALPHA_E, spread=0.0, prediction_error=0.0, beta_uncertainty=0.0,
            is_warmup=False, zscore=z, pair_signal=-z, zscore_lag1=None, beta_post=BETA_E, alpha_post=ALPHA_E,
            observed=True, state_valid=True, segment=0, timestamp=t)
        return eng

    def _feed(self, eng, k, h):
        return eng.process_candle(T0 + k * HR, _pa(h, _pb(k)), _pb(k))

    def test_valid_long_and_short_open_with_decision_metadata(self):
        for z, h0, direction in ((-2.5, -0.03, lc.LONG), (+2.5, +0.03, lc.SHORT)):
            with self.subTest(direction=direction):
                eng = self._engine_at_decision(z, h0)
                d = self._feed(eng, 11, h0)
                self.assertEqual((d.action, d.direction), ("ENTER", direction))
                self.assertEqual(d.trade.entry_decision_timestamp, T0 + 10 * HR)
                self.assertEqual(d.trade.entry_timestamp, T0 + 11 * HR)
                self.assertAlmostEqual(d.trade.entry_held_spread, h0, places=12)

    def test_mismatched_long_and_short_are_rejected_and_recorded(self):
        for z, h0, direction in ((-2.5, +0.03, lc.LONG), (+2.5, -0.03, lc.SHORT)):
            with self.subTest(direction=direction):
                eng = self._engine_at_decision(z, h0)
                d = self._feed(eng, 11, h0)
                self.assertEqual((d.action, d.reason), ("NONE", lc.ENTRY_SIGN_MISMATCH))
                self.assertIsNone(eng.open_trade)
                self.assertEqual(len(eng.entry_sign_mismatches), 1)
                m = eng.entry_sign_mismatches[0]
                self.assertEqual((m.intended_direction, m.decision_timestamp, m.zscore), (direction, T0 + 10 * HR, z))
                self.assertAlmostEqual(m.held_spread, h0, places=12)

    def test_no_same_close_exit_live(self):
        eng = self._engine_at_decision(-2.5, -0.03)
        self.assertEqual(self._feed(eng, 11, +0.01).action, "ENTER")   # h ≥ 0 already at the entry-fill close
        self.assertEqual(self._feed(eng, 12, +0.01).action, "HOLD")    # cutoff E: not an exit-decision close
        d = self._feed(eng, 13, +0.01)                                  # cutoff E + 1 → fill at E + 2
        self.assertEqual((d.action, d.reason), ("EXIT", lc.NORMALISATION))
        self.assertEqual(d.trade.exit_decision_timestamp, T0 + 12 * HR)
        self.assertEqual(d.trade.holding_candles, 2)

    def test_time_stop_exactly_h_live(self):
        eng = self._engine_at_decision(+2.5, +0.03)
        self._feed(eng, 11, +0.03)
        for k in range(12, 11 + H):
            self.assertEqual(self._feed(eng, k, +0.03).action, "HOLD", k)
        d = self._feed(eng, 11 + H, +0.03)
        self.assertEqual((d.action, d.reason, d.trade.holding_candles, d.trade.h_compliant), ("EXIT", lc.TIMESTOP, H, True))
        self.assertEqual(d.trade.exit_decision_timestamp, T0 + (11 + H - 1) * HR)

    def test_missing_exit_fill_candle_live(self):
        eng = self._engine_at_decision(-2.5, -0.03)
        self._feed(eng, 11, -0.03)
        for k in range(12, 11 + H):
            self._feed(eng, k, -0.03)
        d = self._feed(eng, 11 + H + 1, -0.03)       # candle E + H never arrives
        self.assertEqual((d.action, d.reason), ("EXIT", lc.TIMESTOP))
        self.assertEqual(d.trade.holding_candles, H + 1)
        self.assertTrue(d.trade.exit_fill_candle_missing)
        self.assertFalse(d.trade.h_compliant)
        self.assertTrue(eng.requires_reconciliation)  # C5 — and new entries are blocked

    def test_no_reentry_decision_on_the_exit_fill_candle(self):
        eng = self._engine_at_decision(-2.5, -0.03)
        self._feed(eng, 11, +0.01)
        self._feed(eng, 12, +0.01)
        self.assertEqual(self._feed(eng, 13, +0.01).action, "EXIT")   # exit fill at candle 13
        eng._prev_native.zscore = -3.0                                 # adversarial: a strong signal AT the exit-fill candle
        eng._prev_native.observed = True
        d = self._feed(eng, 14, -0.03)
        self.assertEqual((d.action, d.reason), ("NONE", "exit_fill_candle_not_a_decision_cutoff"))

    def test_reconciliation_blocks_entries_until_confirmed(self):
        eng = self._engine_at_decision(-2.5, -0.03)
        eng.requires_reconciliation = True
        self.assertEqual(self._feed(eng, 11, -0.03).reason, "reconciliation_required")
