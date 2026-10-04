# claude code changed: new file — Stage 2D Rev B step 6 (owner D9); rewritten
# for decision C6 (CAUSAL Kalman initialisation). The live runner's real
# bootstrap function is driven by an OFFLINE in-memory kline source (no
# network, no exchange account), and its filter state is compared with the
# research engine's canonical run on the same candles.
#
# HARNESS_L below is a TEST-ONLY init-window length (short synthetic series). The protocol's   # claude code changed: L = 1000 is now frozen (owner baseline)
# canonical L = 1000; the tests use the explicit override below and never choose a protocol value.

import tempfile
from dataclasses import replace
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.core import pairs_bot_runner as runner
from bot.pairs.signal_engine import PairSignalEngine
from bot.quant_core.protocol import CANONICAL, ProtocolValueMissing
from bot.quant_core.seed import (
    causal_init, load_seed, ols_theta, validate_seed, verify_theta_reproduction, write_seed,
)
from bot.research.kalman_filter_engine import KalmanFilterEngine
from bot.research.parity_datasets import dataset_a, seed_intercept
from bot.research.parity_forensics import _cfg

HARNESS_L = 168                                   # test-only (short synthetic series), NOT the protocol value — the canonical L is 1000 (owner baseline)
P = CANONICAL.with_overrides(init_window_candles=HARNESS_L)
# claude code changed: D-8 — the seed is bound to the selection-zone start. dataset_a() starts at the canonical
# init-window start, so the S start is position 1000 (the canonical L); the harness init window is the
# HARNESS_L candles before it. Tests target these positions instead of assuming 'seed = candle L'.
SEED_POS = CANONICAL.require_init_window()
INIT0 = SEED_POS - HARNESS_L
HOUR_MS = 3_600_000


def _engine():
    return KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                              ols_beta=1.3, ols_alpha=seed_intercept())   # ols_* are NOT used for θ₀ by run_canonical


def _canonical(ca, cb, protocol=P):
    eng = _engine()
    return eng.run_canonical(ca, cb, protocol=protocol), eng.seed_record


class OfflineKlines:
    """fetch_ohlcv/milliseconds over fixed in-memory closes (ccxt row shape)."""

    def __init__(self, closes: dict, now_ms: int):
        self.closes, self.now_ms, self.calls = closes, now_ms, 0

    def milliseconds(self):
        return self.now_ms

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=1000):
        self.calls += 1
        s = self.closes[symbol]
        ms = s.index.as_unit("ms").asi8
        sel = [(int(t), float(c)) for t, c in zip(ms, s.to_numpy()) if since is None or t >= since]
        return [[t, c, c, c, c, 0.0] for t, c in sel[:limit]]


class CausalInitLeakageTests(SimpleTestCase):
    """C6: θ₀ may use only candles BEFORE the seed candle."""

    def setUp(self):
        self.ca, self.cb = dataset_a(n=1200)

    def test_theta0_equals_independent_lstsq_on_the_init_window_only(self):
        _, seed = _canonical(self.ca, self.cb)
        la, lb = np.log(self.ca.to_numpy()[INIT0:SEED_POS]), np.log(self.cb.to_numpy()[INIT0:SEED_POS])   # claude code changed: D-8
        X = np.column_stack([lb, np.ones_like(lb)])
        expected, *_ = np.linalg.lstsq(X, la, rcond=None)      # independent derivation
        np.testing.assert_allclose([seed.beta0, seed.alpha0], expected, rtol=1e-10, atol=1e-12)
        self.assertEqual(pd.Timestamp(seed.seed_timestamp), self.ca.index[SEED_POS])   # claude code changed: D-8
        self.assertEqual(pd.Timestamp(seed.seed_timestamp), CANONICAL.require_selection_start())
        self.assertEqual(pd.Timestamp(seed.init_window_start), self.ca.index[INIT0])
        self.assertEqual(pd.Timestamp(seed.init_window_end), self.ca.index[SEED_POS - 1])

    def test_future_candles_cannot_change_the_seed(self):
        _, s1 = _canonical(self.ca, self.cb)
        ca2, cb2 = self.ca.copy(), self.cb.copy()
        ca2.iloc[SEED_POS:] *= np.linspace(1.0, 3.0, len(ca2) - SEED_POS)   # violent change from the seed candle on   # claude code changed: D-8
        cb2.iloc[SEED_POS:] *= 0.5
        b2, s2 = _canonical(ca2, cb2)
        self.assertEqual((s1.beta0, s1.alpha0), (s2.beta0, s2.alpha0))         # bit-identical
        np.testing.assert_array_equal(b2["kalman_beta_pred"].iloc[0], s1.beta0)   # the first prior is θ₀ itself

    def test_positive_control_init_window_candles_do_change_the_seed(self):
        _, s1 = _canonical(self.ca, self.cb)
        ca2 = self.ca.copy()
        ca2.iloc[INIT0 + 10] *= 1.05                         # inside the init window   # claude code changed: D-8
        _, s2 = _canonical(ca2, self.cb)
        self.assertNotEqual(s1.beta0, s2.beta0)

    def test_candles_before_the_init_window_cannot_change_the_seed(self):   # claude code changed: D-8 — new
        _, s1 = _canonical(self.ca, self.cb)
        ca2 = self.ca.copy()
        ca2.iloc[:INIT0] *= 1.7                                  # pre-init data is ignored by the zone-bound seed
        _, s2 = _canonical(ca2, self.cb)
        self.assertEqual((s1.beta0, s1.alpha0), (s2.beta0, s2.alpha0))

    def test_seed_refused_when_history_does_not_cover_the_init_window(self):   # claude code changed: D-8 — new (e.g. FIDA)
        with self.assertRaisesRegex(ValueError, "too little history"):
            causal_init(np.log(self.ca.iloc[INIT0 + 1:]), np.log(self.cb.iloc[INIT0 + 1:]), P)   # unit-level: the D-8 rule itself

    def test_seed_refused_when_the_selection_start_is_not_in_the_data(self):   # claude code changed: D-8 — new
        with self.assertRaisesRegex(ValueError, "not in the data"):
            causal_init(np.log(self.ca.iloc[SEED_POS + 1:]), np.log(self.cb.iloc[SEED_POS + 1:]), P)

    def test_the_prohibited_design_is_detected_by_this_test(self):
        """Adversarial: the forbidden seed — OLS over the whole span assigned to
        the first candle — DOES change when future candles change, so the
        test above would catch it."""
        def prohibited(ca, cb):
            return ols_theta(np.log(ca.to_numpy()), np.log(cb.to_numpy()))
        ca2 = self.ca.copy()
        ca2.iloc[SEED_POS:] *= 1.5   # claude code changed: D-8
        self.assertFalse(np.array_equal(prohibited(self.ca, self.cb), prohibited(ca2, self.cb)))

    def test_output_starts_at_the_seed_candle(self):
        b, seed = _canonical(self.ca, self.cb)
        self.assertEqual(b.index[0], seed.seed_ts)
        self.assertTrue(b["is_warmup"].iloc[:168].all())
        self.assertTrue(b["ols_zscore"].isna().all())       # no look-ahead static baseline

    def test_unset_init_window_fails_closed(self):   # claude code changed: Stage 2D Rev B — L is now 1000 in CANONICAL; an unset L must still fail closed
        with self.assertRaises(ProtocolValueMissing):
            _canonical(self.ca, self.cb, protocol=CANONICAL.with_overrides(init_window_candles=None))

    def test_unobserved_seed_candle_is_refused(self):
        ca2 = self.ca.copy()
        ca2.iloc[SEED_POS] = np.nan   # claude code changed: D-8 — the seed candle
        with self.assertRaisesRegex(ValueError, "not observed"):
            causal_init(np.log(ca2), np.log(self.cb), P)

    def test_seed_record_off_the_selection_start_is_rejected(self):   # claude code changed: D-8 — new
        from dataclasses import replace as dc_replace
        _, seed = _canonical(self.ca, self.cb)
        self.assertTrue(validate_seed(seed, pair_name=seed.pair_name, protocol=P).ok)
        h = pd.Timedelta(hours=1)
        moved = dc_replace(seed, seed_timestamp=(seed.seed_ts + h).isoformat(),
                           init_window_start=(pd.Timestamp(seed.init_window_start) + h).isoformat(),
                           init_window_end=(pd.Timestamp(seed.init_window_end) + h).isoformat())   # still causal, still L long
        r = validate_seed(moved, pair_name=seed.pair_name, protocol=P)
        self.assertFalse(r.ok)
        self.assertIn("selection-zone start", r.reason)

    def test_deterministic_seed(self):
        (b1, s1), (b2, s2) = _canonical(self.ca, self.cb), _canonical(self.ca, self.cb)
        self.assertEqual(s1, s2)
        self.assertTrue(b1[["kalman_beta", "kalman_zscore"]].equals(b2[["kalman_beta", "kalman_zscore"]]))


class CanonicalSeedReplayTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ca, cls.cb = dataset_a(n=2600)
        cls.research, cls.seed = _canonical(cls.ca, cls.cb)
        cls.cfg = _cfg(1.3, seed_intercept(), 0.6, 1000.0)

    def _run(self, seed, closes=None, now_ms=None):
        closes = closes or {"X/USDT": self.ca, "Y/USDT": self.cb}
        last_ms = int(self.ca.index[-1].value // 1_000_000)
        ex = OfflineKlines(closes, now_ms if now_ms is not None else last_ms + HOUR_MS)
        sess = runner.PairSession(pair_config=self.cfg, engine=PairSignalEngine(self.cfg))
        with tempfile.TemporaryDirectory() as d:
            if seed is not None:
                write_seed(seed, d)
            with mock.patch.object(runner, "SEED_REGISTRY_DIR", d), mock.patch.object(runner, "PROTOCOL", P):
                runner.bootstrap_session(sess, ex)
        return sess, ex

    def test_replay_from_causal_seed_reproduces_research_state(self):
        sess, ex = self._run(self.seed)
        self.assertTrue(sess.seed_ok, sess.seed_refusal)
        self.assertGreater(ex.calls, 4)                                   # paginated (1000-candle pages)
        np.testing.assert_array_equal(sess.engine._kf.theta0, [self.seed.beta0, self.seed.alpha0])   # θ₀ from the record, not the config
        last = self.research.iloc[-1]
        np.testing.assert_allclose(sess.engine._kf.theta, [last["kalman_beta"], last["kalman_alpha"]], rtol=0, atol=1e-10)
        self.assertAlmostEqual(sess.engine._kf.P[0, 0], last["beta_uncertainty"], delta=1e-12)
        self.assertAlmostEqual(sess.engine._prev_native.zscore, last["kalman_zscore"], delta=1e-10)
        self.assertEqual(sess.last_candle_ts, int(self.ca.index[-1].value // 1_000_000))

    def test_forming_candle_is_excluded(self):
        last_ms = int(self.ca.index[-1].value // 1_000_000)
        sess, _ = self._run(self.seed, now_ms=last_ms + HOUR_MS // 2)
        self.assertEqual(sess.last_candle_ts, last_ms - HOUR_MS)

    def test_missing_seed_record_refuses_entries(self):
        sess, ex = self._run(None)
        self.assertFalse(sess.seed_ok)
        self.assertIn("no canonical (causal) Kalman seed record", sess.seed_refusal)
        self.assertEqual(ex.calls, 0)

    def test_stale_incompatible_or_non_causal_seed_is_rejected(self):
        for kw, text in (({"methodology_version": "STAGE2D-REVB-1"}, "methodology"),
                         ({"protocol_hash": "deadbeef"}, "protocol hash"),
                         ({"init_window_end": self.seed.seed_timestamp}, "non-causal"),
                         ({"beta0": self.seed.beta0 + 1e-6}, "differs from the seed record"),
                         ({"pair_name": "A_USDT/B_USDT"}, "no canonical (causal) Kalman seed record")):
            with self.subTest(kw=kw):
                sess, _ = self._run(replace(self.seed, **kw))
                self.assertFalse(sess.seed_ok)
                self.assertIn(text, sess.seed_refusal)

    def test_exchange_data_not_reproducing_theta0_is_refused(self):
        ca2 = self.ca.copy()
        ca2.iloc[INIT0 + 20] *= 1.001              # one init-window close differs from the research data   # claude code changed: D-8
        sess, _ = self._run(self.seed, closes={"X/USDT": ca2, "Y/USDT": self.cb})
        self.assertFalse(sess.seed_ok)
        self.assertIn("data-version mismatch", sess.seed_refusal)

    def test_history_not_reaching_the_init_window_refuses_entries(self):
        closes = {"X/USDT": self.ca.iloc[INIT0 + 5:], "Y/USDT": self.cb.iloc[INIT0 + 5:]}   # claude code changed: D-8 — history starts inside the init window
        sess, _ = self._run(self.seed, closes=closes)
        self.assertFalse(sess.seed_ok)

    def test_pre_c6_seed_schema_does_not_load(self):
        with tempfile.TemporaryDirectory() as d:
            (pd.io.common.Path(d) / "kalman_seeds.json").write_text(
                '{"X_USDT/Y_USDT": {"pair_name": "X_USDT/Y_USDT", "seed_timestamp": "2024-01-01T00:00:00+00:00", '
                '"beta0": 1.3, "alpha0": 0.0, "methodology_version": "STAGE2D-REVB-1", "protocol_hash": "x", '
                '"data_first_timestamp": "", "data_last_timestamp": "", "n_grid_candles": 1, "n_observed_candles": 1}}',
                encoding="utf-8")
            self.assertIsNone(load_seed("X_USDT/Y_USDT", d))

    def test_seed_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            write_seed(self.seed, d)
            got = load_seed("X_USDT/Y_USDT", d)
        self.assertEqual(got, self.seed)
        self.assertTrue(validate_seed(got, pair_name="X_USDT/Y_USDT", protocol=P).ok)
        self.assertFalse(validate_seed(got, pair_name="X_USDT/Y_USDT", protocol=CANONICAL).ok)   # different protocol hash

    def test_theta_reproduction_known_answer(self):
        ms = lambda ts: int(ts.value // 1_000_000)
        ca = {ms(t): v for t, v in self.ca.items()}
        cb = {ms(t): v for t, v in self.cb.items()}
        self.assertTrue(verify_theta_reproduction(self.seed, ca, cb).ok)


class LiveCandleSequenceTests(SimpleTestCase):
    """D7/C5: duplicate, out-of-order and off-grid candles are rejected and
    recorded without advancing state; a live polling gap blocks entries."""

    def setUp(self):
        self.ca, self.cb = dataset_a(n=400)
        self.eng = PairSignalEngine(_cfg(1.3, seed_intercept(), 0.6, 1000.0))
        for i in range(300):
            self.eng.process_candle(self.ca.index[i], float(self.ca.iloc[i]), float(self.cb.iloc[i]))

    def test_rejections_leave_state_unchanged(self):
        theta = self.eng._kf.theta.copy()
        t_last = self.ca.index[299]
        for ts, kind in ((t_last, "duplicate"), (t_last - pd.Timedelta(hours=2), "out_of_order"),
                         (t_last + pd.Timedelta(minutes=30), "unexpected_interval")):
            d = self.eng.process_candle(ts, 10.0, 10.0)
            self.assertEqual(d.reason, f"rejected_candle:{kind}")
            np.testing.assert_array_equal(self.eng._kf.theta, theta)
        self.assertEqual([a["kind"] for a in self.eng.candle_anomalies], ["duplicate", "out_of_order", "unexpected_interval"])

    def test_live_gap_requires_reconciliation_and_fabricates_nothing(self):
        i = 301   # candle 300 never arrives
        d = self.eng.process_candle(self.ca.index[i], float(self.ca.iloc[i]), float(self.cb.iloc[i]))
        self.assertTrue(self.eng.requires_reconciliation)
        self.assertEqual(self.eng.reconciliation_gaps, [self.ca.index[300]])
        self.assertEqual(self.eng.missing_candles_stepped, 1)    # a prediction-only step, no invented price
        self.assertEqual(d.action, "NONE")
        self.assertEqual(d.reason, "missing_decision_candle")    # D6: no decision without the decision candle
        self.assertEqual(self.eng._last_observed[0], self.ca.index[i])   # the missing candle is never "observed"
