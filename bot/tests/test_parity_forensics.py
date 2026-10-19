# claude code changed: new file — Stage 2D Phase O forensic parity tests.
# MEASUREMENT ONLY: passing tests assert parity that exists; expectedFailure
# tests encode a Stage 2C §17 requirement the code does not meet yet, each
# naming the correction it needs. No engine logic is changed by these tests.
# Datasets are deterministic (bot/research/parity_datasets.py, seed recorded).

import unittest

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.parity_datasets import (
    DATASET_B_DATA_GAP, DATASET_B_LIVE_ONLY_SKIP, dataset_a, seed_intercept,
)
from bot.research.parity_forensics import _cfg, forensic_replay, research_state, run_live

KW = dict(hedge_ratio=1.3, intercept=seed_intercept(), validated_win_rate=0.6, capital_usdt=1000.0)
STATE_FIELDS = ("beta_prior", "alpha_prior", "innovation", "beta_post", "alpha_post",
                "P00_post", "P11_post", "gain_beta", "zscore",
                "gain_alpha", "innovation_variance_S", "P00_prior", "filtered_residual")   # claude code changed: Stage 2D Rev B — every exposed quantity (D1/D2)
_CACHE = {}


def _report(key, **kw):
    if key not in _CACHE:
        ca, cb = kw.pop("data", dataset_a())
        _CACHE[key] = (forensic_replay(ca, cb, **KW, **kw), ca, cb)
    return _CACHE[key]


def _fields(report):
    return {f.name: f for f in report.state_fields}


def _matched(report):
    tp = report.trade_pairs
    return tp[tp["live_fill"].notna()]


class DatasetACleanPair(SimpleTestCase):
    def setUp(self):
        self.r, self.ca, self.cb = _report("A")

    def test_kalman_and_zscore_state_identical(self):
        f = _fields(self.r)
        for name in STATE_FIELDS:
            self.assertTrue(f[name].equal, f"{name}: max|err| {f[name].max_abs_error}, rel {f[name].max_rel_error}")
            self.assertEqual(f[name].only_research + f[name].only_live, 0)

    def test_every_research_trade_has_a_live_counterpart(self):
        self.assertEqual(len(_matched(self.r)), len(self.r.trade_pairs))

    def test_entry_cutoff_fill_candle_and_fill_price_identical(self):
        m = _matched(self.r)
        self.assertTrue((m["research_decision"] == m["live_decision_candle"]).all())
        self.assertTrue((m["research_fill"] == m["live_fill"]).all())
        self.assertLessEqual(np.abs(m["research_entry_price_a"] - m["live_entry_price_a"]).max(), 1e-12)

    def test_measured_live_decision_lag_is_one_candle(self):
        self.assertEqual(self.r.decision_lag_candles, 1)

    # claude code changed: Stage 2D Rev B — D1/D2 were expectedFailure; both
    # paths now run bot/quant_core/kalman.py and decide on the posterior.
    def test_entry_beta_is_the_posterior_at_the_cutoff(self):
        """D2: β_e = θ(t|t) on both sides (live used θ(t|t−1) before Rev B)."""
        m = _matched(self.r)
        self.assertLessEqual(np.abs(m["research_entry_beta"] - m["live_entry_beta"]).max(), 1e-10)

    def test_decision_uncertainty_definition_identical(self):
        """D1: one uncertainty definition, posterior P(t|t)[0,0] (live used the prior)."""
        self.assertTrue(_fields(self.r)["uncertainty_used_for_decisions"].equal)

    # claude code changed: Stage 2D Rev B Steps 9–10 — D3/D4 were expectedFailure;
    # both paths now run bot/quant_core/lifecycle.py (exit decided at u ≥ E+1,
    # filled at u+d; no partial exit).
    def test_time_stop_exit_fills_identically(self):
        """D3: exit decisions and fills on the same candles on both sides."""
        m = _matched(self.r)
        self.assertTrue((m["research_exit_fill"] == m["live_exit_fill"]).all())
        self.assertTrue((m["research_exit_decision"] == pd.to_datetime(m["live_exit_decision"])).all())

    def test_position_lifecycle_identical(self):
        """D4: same reasons, holding periods and P&L. Research exports
        net_pnl_usdt rounded to cents and mark_pnl_usdt to 1e-6, so those are
        the comparison resolutions (the unrounded values are not exported)."""
        m = _matched(self.r)
        self.assertTrue((m["research_exit_reason"] == m["live_exit_reason"]).all())
        self.assertTrue((m["research_holding_candles"] == m["live_holding_candles"]).all())
        self.assertLessEqual(np.abs(m["research_mark_pnl"] - m["live_mark_pnl_modelled"]).max(), 5e-7)
        self.assertLessEqual(np.abs(m["research_net_pnl"] - m["live_net_pnl_modelled"]).max(), 0.005)
        self.assertFalse(set(m["research_exit_reason"]) - {"NORMALISATION", "TIMESTOP"})   # no partial/target/stop exits

    def test_live_entry_record_carries_its_decision_timestamp(self):   # claude code changed: Stage 2D Rev B (D5) — was expectedFailure
        """D5: the immutable entry record names its decision cutoff, and every
        decision field (β_e, α_e, held spread) equals research's."""
        m = _matched(self.r)
        self.assertTrue(m["live_entry_record_has_decision_ts"].all())
        self.assertTrue((pd.to_datetime(m["live_entry_decision_ts"]) == m["research_decision"]).all())
        for col in ("entry_alpha", "entry_held_spread"):
            d = np.abs(m[f"research_{col}"].astype(float) - m[f"live_{col}"].astype(float))
            self.assertTrue(np.isfinite(d).all(), col)
            self.assertLessEqual(d.max(), 1e-10, col)

    def test_research_defaults_equal_live_configuration(self):   # claude code changed: Stage 2D Rev B (D10) — was expectedFailure
        """D10: the same RULE SET (rule set, protocol hash, d, f). Capital and
        minimum position are deployment configuration, reported apart."""
        self.assertEqual(self.r.config_differences, {})
        self.assertEqual(set(self.r.deployment_differences), {"sizer.capital", "sizer.min_position_usdt"})


class DatasetBGaps(SimpleTestCase):
    def test_data_gap_is_handled_identically_by_both_paths(self):   # claude code changed: Stage 2D Rev B — both now keep the gap on the grid (was: both compressed it)
        """D8: both paths step the 3 missing hours as prediction-only grid
        candles (P += gQ); state on every observed candle is identical."""
        ca, cb = dataset_a()
        rows = [i for i in range(len(ca)) if i not in DATASET_B_DATA_GAP]
        r = forensic_replay(ca, cb, research_rows=rows, live_skip=DATASET_B_DATA_GAP, **KW)
        f = _fields(r)
        for name in STATE_FIELDS:
            self.assertTrue(f[name].equal, name)

    def test_research_state_keeps_missing_hours_as_gaps(self):   # claude code changed: Stage 2D Rev B (D8) — was expectedFailure
        """D8: the grid keeps every hour and P(t+g|t) = P(t|t) + g·Q across the gap."""
        ca, cb = dataset_a()
        rows = [i for i in range(len(ca)) if i not in DATASET_B_DATA_GAP]
        b = research_state(ca.iloc[rows], cb.iloc[rows], KW["hedge_ratio"], KW["intercept"])
        self.assertTrue(((b.index[1:] - b.index[:-1]) == pd.Timedelta(hours=1)).all())
        g0, g = DATASET_B_DATA_GAP[0], len(DATASET_B_DATA_GAP)
        self.assertFalse(b["is_observed"].iloc[g0:g0 + g].any())
        self.assertAlmostEqual(b["beta_uncertainty"].iloc[g0 + g - 1],
                               b["beta_uncertainty"].iloc[g0 - 1] + g * 1e-5, places=15)
        self.assertTrue(b["state_valid"].all())
        self.assertEqual(b["kalman_segment"].max(), 0)

    def test_gap_beyond_g_max_reseeds_both_paths_identically(self):   # claude code changed: Stage 2D Rev B (D8) — new
        """D8: a 5-hour gap (> g_max = 3) invalidates the state; both paths
        re-seed with θ₀, P₀, restart warmup and the z history identically."""
        ca, cb = dataset_a()
        gap = tuple(range(700, 705))
        rows = [i for i in range(len(ca)) if i not in gap]
        r = forensic_replay(ca, cb, research_rows=rows, live_skip=gap, **KW)
        f = _fields(r)
        for name in STATE_FIELDS:
            self.assertTrue(f[name].equal, name)
        b = research_state(ca.iloc[rows], cb.iloc[rows], KW["hedge_ratio"], KW["intercept"])
        after = b.index[705]
        self.assertEqual(b.loc[after, "kalman_segment"], 1)
        self.assertAlmostEqual(b.loc[after, "kalman_beta_pred"], KW["hedge_ratio"], places=15)
        self.assertTrue(b["is_warmup"].iloc[705:705 + 168].all())
        self.assertTrue(b["kalman_zscore"].iloc[700:705 + 168 + 167].isna().all())   # warmup, then 168 valid values needed

    def test_candle_skipped_by_live_only_is_detected(self):
        ca, cb = dataset_a()
        rows = [i for i in range(len(ca)) if i not in DATASET_B_DATA_GAP]
        r = forensic_replay(ca, cb, research_rows=rows,
                            live_skip=DATASET_B_DATA_GAP + DATASET_B_LIVE_ONLY_SKIP, **KW)
        f = _fields(r)
        self.assertFalse(f["beta_post"].equal)
        self.assertFalse(f["zscore"].equal)


class DatasetCLatency(SimpleTestCase):
    def setUp(self):
        self.r, self.ca, self.cb = _report("A")
        self.t0 = self.r.trade_pairs.iloc[0]["research_decision"]
        self.i0 = self.ca.index.get_loc(self.t0)

    def test_no_same_candle_entry_fill(self):
        first = self.r.trade_pairs.iloc[0]
        self.assertNotEqual(self.ca.iloc[self.i0], self.ca.iloc[self.i0 + 1])          # the prices differ materially
        self.assertAlmostEqual(first["research_entry_price_a"], self.ca.iloc[self.i0 + 1], places=12)

    def test_research_skips_when_the_fill_candle_is_missing(self):
        n = len(self.ca)
        rows = [i for i in range(n) if i != self.i0 + 1]
        r = forensic_replay(self.ca, self.cb, research_rows=rows, live_skip=(self.i0 + 1,), **KW)
        self.assertNotIn(self.t0, set(r.trade_pairs["research_decision"]) if not r.trade_pairs.empty else set())

    def test_live_skips_when_the_fill_candle_is_missing(self):   # claude code changed: Stage 2D Rev B (D6) — was expectedFailure; grid stepping makes the missing candle the decision bundle, which has no z
        """D6: missing entry fill → no trade (live acted on the stale decision before Rev B)."""
        n = len(self.ca)
        rows = [i for i in range(n) if i != self.i0 + 1]
        r = forensic_replay(self.ca, self.cb, research_rows=rows, live_skip=(self.i0 + 1,), **KW)
        self.assertFalse(any(e["fill_ts"] == self.ca.index[self.i0 + 2] for e in r.live_entries))


class ProductionBootstrap(SimpleTestCase):
    """D9. claude code changed: Stage 2D Rev B — the runner no longer bootstraps
    trading pairs from the last ~999 candles; it replays from the canonical seed
    candle (bot/tests/test_canonical_seed.py drives the real runner function).
    The measurement below is kept as the evidence for WHY a shortened
    bootstrap is refused."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ca, cb = dataset_a(n=3000)
        cls.S = 1000
        cls.b = research_state(ca, cb, KW["hedge_ratio"], KW["intercept"])
        cfg = _cfg(KW["hedge_ratio"], KW["intercept"], 0.6, 1000.0)
        short = run_live(ca, cb, cfg, bootstrap_until=cls.S + 999, skip=tuple(range(cls.S)))
        full = run_live(ca, cb, cfg, bootstrap_until=cls.S + 999)
        cls.L_short = short.state.reindex(cls.b.index)
        cls.L_full = full.state.reindex(cls.b.index)

    def test_shortened_bootstrap_diverges_and_is_therefore_refused(self):
        d = (self.b["kalman_beta"] - self.L_short["beta_post"]).abs().iloc[self.S + 999:]
        self.assertGreater(d.max(), 1e-3)          # measurement: a real, persistent divergence

    def test_live_state_equals_research_state(self):   # claude code changed: Stage 2D Rev B (D9) — was expectedFailure; now replays from the seed candle
        """D9: replay from the canonical seed → identical model state."""
        for col, lc in (("kalman_beta", "beta_post"), ("kalman_alpha", "alpha_post"), ("beta_uncertainty", "P00_post")):
            d = (self.b[col] - self.L_full[lc]).abs()
            self.assertLessEqual(d.max(), 1e-10, col)


class CausalityAndDeterminism(SimpleTestCase):
    def test_outputs_up_to_T_unchanged_by_prices_after_T(self):
        ca, cb = dataset_a()
        n, T = len(ca), 1100
        ca_p, cb_p = ca.copy(), cb.copy()
        ca_p.iloc[T + 1:] *= np.linspace(1.0, 1.4, n - T - 1)
        cb_p.iloc[T + 1:] *= 0.9
        Tts = ca.index[T]
        cols = ["kalman_beta", "kalman_beta_pred", "kalman_spread", "kalman_zscore", "beta_uncertainty"]
        b1 = research_state(ca, cb, KW["hedge_ratio"], KW["intercept"])
        b2 = research_state(ca_p, cb_p, KW["hedge_ratio"], KW["intercept"])
        self.assertTrue(b1.loc[:Tts, cols].equals(b2.loc[:Tts, cols]))                    # research
        self.assertFalse(b1.loc[Tts:, cols].iloc[2:].equals(b2.loc[Tts:, cols].iloc[2:]))  # positive control
        r1, r2 = forensic_replay(ca, cb, **KW), forensic_replay(ca_p, cb_p, **KW)
        closed = lambda r: r.research_trades[pd.to_datetime(r.research_trades["exit_timestamp"]) <= Tts].reset_index(drop=True)
        self.assertTrue(closed(r1).equals(closed(r2)))                                       # research decisions
        upto = lambda ev: [e for e in ev if e["fill_ts"] <= Tts]
        self.assertEqual(upto(r1.live_entries), upto(r2.live_entries))                       # live
        self.assertEqual(upto(r1.live_exits), upto(r2.live_exits))

    def test_replay_is_deterministic(self):
        ca, cb = dataset_a()
        r1, r2 = forensic_replay(ca, cb, **KW), forensic_replay(ca, cb, **KW)
        self.assertTrue(r1.state_table().equals(r2.state_table()))
        self.assertTrue(r1.trade_pairs.equals(r2.trade_pairs))
        self.assertEqual(r1.live_entries, r2.live_entries)
        self.assertEqual(r1.live_exits, r2.live_exits)
