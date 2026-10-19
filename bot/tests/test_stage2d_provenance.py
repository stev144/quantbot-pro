# claude code changed: new file — Stage 2D Rev B D-1 / D-2 / D-3 / D-4 / D-5 /
# G-1 / L = 1000 / Design A. Governance tests: provenance must be written,
# loaded, validated, rejected when incompatible, and preserved; legacy data
# must never become canonical by passing through a canonical engine.
# Offline only — no exchange, no network. Synthetic data from
# bot/research/parity_datasets (seed recorded there).

import contextlib
import io
import json
import logging
import math
import shutil
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase, TestCase

from bot.core import pairs_bot_runner as runner
from bot.journal.models import SignalRejection, TradeRecord
from bot.pairs import canonical_eligibility as ce
from bot.pairs.kalman_online import NativeStepResult
from bot.pairs.margin_adapter import MarginFillResult
from bot.pairs.signal_engine import PairSignalEngine
from bot.pairs.trade_logger import PairsTradeLogger
from bot.quant_core import lifecycle as lc
from bot.quant_core import provenance as prov
from bot.quant_core.protocol import CANONICAL, METHODOLOGY_VERSION
from bot.quant_core.seed import KalmanSeed, ols_theta, seed_id, validate_design_a, validate_seed
from bot.research.entry_exit_engine import CanonicalProvenanceError, EntryExitEngine
from bot.research.kalman_filter_engine import KalmanFilterEngine
from bot.research.parity_datasets import dataset_a, seed_intercept
from bot.research.parity_forensics import _cfg

P = CANONICAL.with_overrides(init_window_candles=168)   # TEST-ONLY L for short synthetic series (canonical L = 1000)
HR = pd.Timedelta(hours=1)


def _quiet(fn, *a, **kw):
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **kw)
    finally:
        logging.disable(logging.NOTSET)


def _kfe():
    return KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                              ols_beta=1.3, ols_alpha=seed_intercept())


def _canonical_dataset(tmp, n=1500, protocol=P):
    ca, cb = dataset_a(n=n)
    eng = _kfe()
    _quiet(eng.run_canonical, ca, cb, protocol=protocol, output_dir=tmp)
    return Path(tmp) / "canonical" / "X_USDT_Y_USDT_kalman.csv", eng


def _eee(out, protocol=P, rule_set="CANONICAL", **costs):   # claude code changed: F-12 — optional borrow rates (canonical status needs complete costs)
    return EntryExitEngine(output_dir=str(out), validated_win_rate=0.6, rule_set=rule_set, protocol=protocol,
                           capital_usdt=10_000.0, pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", **costs)


# claude code changed: owner decision (borrow row) — canonical financing = RECORDED perpetual funding; a constant
# borrow rate is now an assumption (BORROW_RATE_ASSUMED) and can no longer make an output CANONICAL.
_FUNDING = pd.Series(0.0001, index=pd.date_range("2021-09-01", "2023-12-31", freq="8h", tz="UTC"))
BORROW = {"funding_rates_a": _FUNDING, "funding_rates_b": _FUNDING}


# ── D-5 ─────────────────────────────────────────────────────────────────────
class DataVersionTests(SimpleTestCase):
    def setUp(self):
        idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
        g = np.random.default_rng(7)
        c = 10 + np.cumsum(g.normal(0, 0.1, 48))
        self.df = pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": g.uniform(1, 9, 48)}, index=idx)
        self.cols = ["open", "high", "low", "close", "volume"]

    def _dv(self, df=None, sym="X", tf="1h", cols=None):
        return prov.data_version({sym: self.df if df is None else df}, timeframe=tf, columns=cols or self.cols)

    def test_identical_data_identical_fingerprint(self):
        self.assertEqual(self._dv().data_version, self._dv(self.df.copy()).data_version)
        self.assertTrue(prov.is_sha256(self._dv().data_version))

    def test_any_change_changes_the_fingerprint(self):
        base = self._dv().data_version
        price = self.df.copy(); price.iloc[10, 3] += 1e-9
        vol = self.df.copy(); vol.iloc[5, 4] += 1.0
        ts = self.df.copy(); ts.index = ts.index.where(ts.index != ts.index[20], ts.index[20] + pd.Timedelta(minutes=0) + HR * 100)
        removed = self.df.drop(self.df.index[17])
        for name, df in (("price", price), ("volume", vol), ("timestamp", ts), ("row removed", removed)):
            with self.subTest(name):
                self.assertNotEqual(self._dv(df).data_version, base)
        self.assertNotEqual(self._dv(sym="Y").data_version, base)
        self.assertNotEqual(self._dv(tf="2h").data_version, base)
        self.assertNotEqual(self._dv(cols=["close"]).data_version, base)            # close-only never collides with OHLCV

    def test_row_order_is_normalised_by_design(self):
        shuffled = self.df.sample(frac=1.0, random_state=3)
        self.assertEqual(self._dv(shuffled).data_version, self._dv().data_version)

    def test_missing_candle_is_counted_and_recorded(self):
        dv = self._dv(self.df.drop(self.df.index[17]))
        self.assertEqual((dv.n_grid_candles, dv.n_rows_observed, dv.n_missing), (48, 47, 1))
        self.assertEqual(dv.start, self.df.index[0].isoformat())

    def test_conflicting_duplicate_raises(self):
        dup = pd.concat([self.df, self.df.iloc[[3]].assign(close=999.0)])
        with self.assertRaises(ValueError):
            self._dv(dup)


# ── D-4, L = 1000, Design A ─────────────────────────────────────────────────
class CanonicalDatasetSeparationTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_canonical_dataset_is_accepted_and_labelled_with_lineage(self):
        # claude code changed: F-12 — borrow is now modelled here: since 2026-10-03 a CANONICAL label also
        # requires complete costs (the no-borrow case is pinned by the test below). Assertions unchanged.
        csv, eng = _canonical_dataset(self.tmp)
        out = self.tmp / "o"
        _, summary, _ = _quiet(_eee(out, **BORROW).run, kalman_csv=str(csv))
        row = summary.iloc[0]
        self.assertEqual((row["provenance_status"], row["methodology_version"]), (prov.CANONICAL, METHODOLOGY_VERSION))
        self.assertEqual(row["protocol_hash"], P.config_hash())
        self.assertEqual(row["data_version"], eng.seed_record.data_version)
        self.assertEqual(row["seed_id"], seed_id(eng.seed_record))
        self.assertEqual(row["seed_design"], "A_PRE_SELECTION")
        sp = prov.read_provenance(out / "X_Y_strategy_summary.csv")
        self.assertTrue(prov.validate_canonical_provenance(sp, kind="STRATEGY_SUMMARY", methodology_version=METHODOLOGY_VERSION,
                                                           protocol_hash=P.config_hash(), artifact=out / "X_Y_strategy_summary.csv").ok)

    def test_canonical_dataset_without_modelled_borrow_is_not_canonical(self):   # claude code changed: F-12 — new
        csv, eng = _canonical_dataset(self.tmp)
        out = self.tmp / "o"
        _, summary, _ = _quiet(_eee(out).run, kalman_csv=str(csv))
        row = summary.iloc[0]
        self.assertEqual(row["provenance_status"], prov.UNCERTIFIED)
        self.assertFalse(bool(row["cost_complete"]))
        self.assertEqual(row["methodology_version"], METHODOLOGY_VERSION)     # lineage facts kept
        self.assertEqual(row["seed_id"], seed_id(eng.seed_record))
        sp = prov.read_provenance(out / "X_Y_strategy_summary.csv")
        self.assertEqual(sp.status, prov.UNCERTIFIED)                         # summary row and sidecar agree
        self.assertFalse(prov.validate_canonical_provenance(sp, kind="STRATEGY_SUMMARY", methodology_version=METHODOLOGY_VERSION,
                                                            protocol_hash=P.config_hash(), artifact=out / "X_Y_strategy_summary.csv").ok)

    def test_the_audit_adversarial_case_is_rejected(self):
        """D-4 demonstration from the pre-Step-12 audit: a LEGACY look-ahead-seeded
        Kalman file through the canonical engine. Must be refused, emit nothing,
        and never carry a canonical label."""
        ca, cb = dataset_a(n=1500)
        eng = _kfe()
        la, lb = eng._to_log_prices(eng._align_prices(ca, cb))
        b = eng._run_filter(la, lb, eng._initialise_state(la, lb))
        b = eng._calculate_dynamic_spread(b, la, lb)
        b = eng._calculate_dynamic_zscore(b)
        b["is_training_window"] = np.arange(len(b)) < 1000
        out = b.rename_axis("timestamp").reset_index()
        out["close_a"], out["close_b"] = ca.to_numpy(), cb.to_numpy()
        csv = self.tmp / "X_USDT_Y_USDT_kalman.csv"
        out.to_csv(csv, index=False)
        o = self.tmp / "o"
        with self.assertRaisesRegex(CanonicalProvenanceError, "not CANONICAL"):
            _quiet(_eee(o).run, kalman_csv=str(csv))
        self.assertFalse(o.exists() and any(o.iterdir()))              # no summary, no trade log, nothing
        # a forged CANONICAL sidecar copied from a real dataset is rejected too (digest / seed start)
        real_csv, _ = _canonical_dataset(self.tmp / "real")
        shutil.copy(prov.sidecar_path(real_csv), prov.sidecar_path(csv))
        with self.assertRaisesRegex(CanonicalProvenanceError, "artifact bytes"):
            _quiet(_eee(o).run, kalman_csv=str(csv))

    def _tamper(self, csv, **changes):
        sc = prov.sidecar_path(csv)
        d = json.loads(sc.read_text(encoding="utf-8"))
        for k, v in changes.items():
            if k.startswith("seed."):
                d["extra"]["seed"][k[5:]] = v
            else:
                d[k] = v
        sc.write_text(json.dumps(d), encoding="utf-8")

    def test_every_provenance_mutation_is_rejected(self):
        cases = (
            ("missing provenance", None, "not CANONICAL"),
            ("legacy status", {"status": prov.LEGACY}, "not CANONICAL"),
            ("wrong methodology", {"methodology_version": "STAGE2D-REVB-2"}, "methodology"),
            ("wrong protocol hash", {"protocol_hash": "0" * 64}, "protocol hash"),
            ("no data version", {"data_version": "NOT_RECORDED"}, "data version"),
            ("wrong data fingerprint", {"data_version": "a" * 64}, "data version"),
            ("wrong seed design", {"seed.seed_design": "B_SELECTION_WARMUP"}, "seed design"),
            ("wrong seed lineage", {"seed.beta0": 9.9}, "lineage id"),
            ("non-causal seed", {"seed.init_window_end": "2030-01-01T00:00:00+00:00"}, "non-causal"),
            ("wrong L", {"seed.init_window_start": "2021-10-16T00:00:00+00:00"}, "init window length"),   # claude code changed: D-8 — the seed is now at the S start (2021-11-07); a 504-candle window (≠ L) that still ends before it. The old 2023 date now lies AFTER the window end and tripped the earlier causality check instead
        )
        for name, change, msg in cases:
            with self.subTest(name):
                d = self.tmp / name.replace(" ", "_")
                csv, _ = _canonical_dataset(d)
                if change is None:
                    prov.sidecar_path(csv).unlink()
                else:
                    self._tamper(csv, **change)
                with self.assertRaisesRegex(CanonicalProvenanceError, msg):
                    _quiet(_eee(d / "o").run, kalman_csv=str(csv))

    def test_modified_dataset_bytes_are_rejected(self):
        csv, _ = _canonical_dataset(self.tmp)
        df = pd.read_csv(csv)
        df.loc[500, "kalman_zscore"] = 4.9
        df.to_csv(csv, index=False)
        with self.assertRaisesRegex(CanonicalProvenanceError, "artifact bytes"):
            _quiet(_eee(self.tmp / "o").run, kalman_csv=str(csv))

    def test_engine_protocol_must_match_the_dataset(self):
        csv, _ = _canonical_dataset(self.tmp)
        with self.assertRaisesRegex(CanonicalProvenanceError, "protocol hash"):
            _quiet(_eee(self.tmp / "o", protocol=CANONICAL).run, kalman_csv=str(csv))   # L = 1000 protocol vs L = 168 dataset

    def test_uncertified_mode_never_claims_canonical(self):
        csv, _ = _canonical_dataset(self.tmp)
        prov.sidecar_path(csv).unlink()
        _, summary, _ = _quiet(_eee(self.tmp / "o", rule_set="CANONICAL_UNCERTIFIED").run, kalman_csv=str(csv))
        row = summary.iloc[0]
        self.assertEqual((row["provenance_status"], row["methodology_version"], row["protocol_hash"]),
                         (prov.UNCERTIFIED, prov.UNCERTIFIED, ""))
        sp = prov.read_provenance(self.tmp / "o" / "X_Y_strategy_summary.csv")
        self.assertEqual(sp.status, prov.UNCERTIFIED)

    def test_canonical_protocol_locks_l_1000_and_design_a(self):
        self.assertEqual((CANONICAL.init_window_candles, CANONICAL.seed_design), (1000, "A_PRE_SELECTION"))
        self.assertEqual(METHODOLOGY_VERSION, "STAGE2D-REVB-4")   # claude code changed: D-8 — deliberate bump (seed bound to the selection-zone start)
        self.assertEqual(CANONICAL.selection_start_utc, "2021-11-07T00:00:00+00:00")   # claude code changed: D-8 — the signed S start
        ca, cb = dataset_a(n=2600)
        eng = _kfe()
        _quiet(eng.run_canonical, ca, cb, protocol=CANONICAL)          # the real baseline, L = 1000
        s = eng.seed_record
        self.assertEqual((pd.Timestamp(s.init_window_end) - pd.Timestamp(s.init_window_start)) / HR + 1, 1000)
        self.assertEqual(s.seed_ts, ca.index[1000])
        self.assertTrue(validate_seed(s, pair_name=s.pair_name, protocol=CANONICAL).ok)
        self.assertFalse(validate_seed(s, pair_name=s.pair_name, protocol=CANONICAL.with_overrides(init_window_candles=504)).ok)


class DesignALeakageTests(SimpleTestCase):
    def setUp(self):
        self.ca, self.cb = dataset_a(n=1500)
        eng = _kfe()
        _quiet(eng.run_canonical, self.ca, self.cb, protocol=P)
        self.seed = eng.seed_record

    def test_negative_control_causal_seed_accepted(self):
        self.assertTrue(validate_design_a(self.seed, self.ca, self.cb, protocol=P).ok)

    def test_positive_control_seed_fitted_on_future_selection_data_rejected(self):
        la, lb = np.log(self.ca.to_numpy()), np.log(self.cb.to_numpy())
        future = ols_theta(la[168:1168], lb[168:1168])        # fitted on SELECTION-zone candles, placed at the start
        bad = replace(self.seed, beta0=float(future[0]), alpha0=float(future[1]))
        r = validate_design_a(bad, self.ca, self.cb, protocol=P)
        self.assertFalse(r.ok)
        self.assertIn("data-version mismatch", r.reason)
        whole = ols_theta(la, lb)                              # the legacy-style whole-span fit
        self.assertFalse(validate_design_a(replace(self.seed, beta0=float(whole[0]), alpha0=float(whole[1])),
                                           self.ca, self.cb, protocol=P).ok)

    def test_init_window_must_end_before_the_selection_zone(self):
        r = validate_design_a(self.seed, self.ca, self.cb, protocol=P, selection_start=self.ca.index[100])
        self.assertFalse(r.ok)
        self.assertIn("not before the selection zone", r.reason)


# ── D-3 ─────────────────────────────────────────────────────────────────────
class CanonicalEligibilityTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        eng = _kfe()
        _quiet(eng.run_canonical, *dataset_a(n=1200), protocol=P)
        self.seed = eng.seed_record

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ev(self, **kw):
        e = {"kind": "COINTEGRATION_EG", "status": prov.CANONICAL, "methodology_version": METHODOLOGY_VERSION,
             "protocol_hash": P.config_hash(), "data_version": self.seed.data_version, "run_id": "r1"}
        return {**e, **kw}

    def _write(self, evidence=None, verdict=ce.ELIGIBLE, seed=None):
        return ce.write_eligibility("X_USDT/Y_USDT", seed=seed or self.seed, verdict=verdict,
                                    evidence=evidence or [self._ev()], protocol=P, registry_dir=self.tmp, producer="test")

    def _check(self, seed="default"):
        return ce.check_entry_eligibility("X_USDT/Y_USDT", seed=self.seed if seed == "default" else seed,
                                          protocol=P, registry_dir=self.tmp)

    def test_canonical_verdict_is_eligible(self):
        self._write()
        self.assertTrue(self._check().ok)

    def test_missing_or_legacy_evidence_blocks(self):
        self.assertFalse(self._check().ok)                       # nothing written
        legacy = self.tmp / "X_USDT_Y_USDT.json"                 # a legacy verdict copied into the canonical slot
        legacy.write_text(json.dumps({"pair_name": "X_USDT/Y_USDT", "verdict": ce.ELIGIBLE}), encoding="utf-8")
        prov.write_provenance(legacy, prov.legacy_provenance(legacy, kind=ce.KIND, producer="legacy"))
        r = self._check()
        self.assertFalse(r.ok)
        self.assertIn("not CANONICAL", r.reason)
        self.assertFalse(self._check(seed=None).ok)              # no validated seed

    def test_incompatible_evidence_blocks(self):
        cases = (("missing methodology", [self._ev(methodology_version="")], "unversioned"),
                 ("mixed-version evidence", [self._ev(), self._ev(kind="PERMUTATION", methodology_version="LEGACY-PRE-STAGE2D")], "mixed"),
                 ("legacy evidence item", [self._ev(status=prov.LEGACY)], "mixed"),
                 ("verdict ineligible", None, "INELIGIBLE"))
        for name, ev, msg in cases:
            with self.subTest(name):
                self._write(evidence=ev, verdict="INELIGIBLE" if ev is None else ce.ELIGIBLE)
                r = self._check()
                self.assertFalse(r.ok)
                self.assertIn(msg, r.reason)

    def test_wrong_protocol_or_data_version_blocks(self):
        self._write()
        r = ce.check_entry_eligibility("X_USDT/Y_USDT", seed=self.seed, protocol=CANONICAL, registry_dir=self.tmp)
        self.assertFalse(r.ok)
        self.assertIn("protocol hash", r.reason)
        other = replace(self.seed, data_version="b" * 64)
        self.assertIn("data version", self._check(seed=other).reason)

    def test_tampered_verdict_bytes_block(self):
        p = self._write()
        p.write_text(p.read_text(encoding="utf-8").replace("ELIGIBLE", "ELIGIBLE "), encoding="utf-8")
        self.assertIn("artifact bytes", self._check().reason)


# ── G-1 ─────────────────────────────────────────────────────────────────────
class LegacyProvenanceTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip_and_rejection(self):
        f = self.tmp / "cointegration_pairs.csv"
        f.write_text("pair_name,passes_filters\nA/B,True\n", encoding="utf-8")
        prov.write_legacy_provenance(f, kind="COINTEGRATION_SCAN", producer="bot.research.cointegration_engine")
        p = prov.read_provenance(f)
        self.assertEqual((p.status, p.kind, p.methodology_version), (prov.LEGACY, "COINTEGRATION_SCAN", "LEGACY-PRE-STAGE2D"))
        self.assertEqual(p.artifact_sha256, prov.file_sha256(f))
        self.assertTrue(p.run_id)
        self.assertEqual(prov.read_provenance(f), p)                                   # serialisation preserved
        self.assertFalse(prov.validate_canonical_provenance(p, kind="COINTEGRATION_SCAN", methodology_version=METHODOLOGY_VERSION,
                                                            protocol_hash=CANONICAL.config_hash()).ok)

    def test_no_or_corrupt_sidecar_reads_as_legacy_unknown(self):
        f = self.tmp / "permutation_verdict.csv"
        f.write_text("edge_appears_real\nTrue\n", encoding="utf-8")
        self.assertEqual((prov.read_provenance(f).status, prov.read_provenance(f).methodology_version), (prov.LEGACY, "UNKNOWN"))
        prov.sidecar_path(f).write_text("{not json", encoding="utf-8")
        self.assertEqual(prov.read_provenance(f).status, prov.LEGACY)

    def test_permutation_engine_writes_a_legacy_sidecar(self):
        from bot.research.permutation_test_engine import PermutationTestEngine
        _quiet(PermutationTestEngine._save_outputs, mock.Mock(), self.tmp, "A/B", {"win_rate": 0.6}, [{"win_rate": 0.5}],
               {"edge_appears_real": True})
        p = prov.read_provenance(self.tmp / "permutation_verdict.csv")
        self.assertEqual((p.status, p.kind), (prov.LEGACY, "PERMUTATION_VERDICT"))


# ── D-1 / D-2 persistence (database) ────────────────────────────────────────
def _fill(side, qty, price, symbol="X/USDT"):
    return MarginFillResult(order_id=f"t-{side}-{qty}", symbol=symbol, side=side, filled_qty=qty, fill_price=price,
                            fee_usdt=0.01, fee_asset="USDT", fees=((0.01, "USDT"),))


class ReconciliationProvenancePersistenceTests(TestCase):
    def test_flag_persists_and_clean_exits_are_unmarked(self):
        lg = PairsTradeLogger()
        affected, clean = uuid.uuid4(), uuid.uuid4()
        for tid, sym in ((affected, "X/USDT"), (clean, "Z/USDT")):
            lg.log_leg_entry(tid, "A", sym, "BUY", _fill("buy", 10.0, 1.0, sym), 10.0, "X_USDT/Y_USDT")
        lg.log_leg_exit(affected, "A", _fill("sell", 10.0, 1.1), "TIMESTOP",
                        reconciliation_affected=True, reconciliation_gaps="2024-01-01T05:00:00+00:00")
        lg.log_leg_exit(clean, "A", _fill("sell", 10.0, 1.1, "Z/USDT"), "NORMALISATION")
        # "restart": nothing in memory — read back from the database only
        a = TradeRecord.objects.get(pair_trade_id=affected, leg="A")
        c = TradeRecord.objects.get(pair_trade_id=clean, leg="A")
        self.assertTrue(a.reconciliation_affected)
        self.assertEqual(a.reconciliation_gaps, "2024-01-01T05:00:00+00:00")
        self.assertFalse(c.reconciliation_affected)
        self.assertEqual(TradeRecord.objects.filter(reconciliation_affected=False, pair_trade_id__isnull=False).count(), 1)


class SignalRejectionPersistenceTests(TestCase):
    def test_rejection_is_durable_and_idempotent(self):
        m = lc.EntrySignMismatch(pd.Timestamp("2024-03-01 10:00", tz="UTC"), lc.LONG, -2.5, 0.03, 0.2, 1.3, 23.6, 10.0)
        lg = PairsTradeLogger()
        lg.log_signal_rejection("X_USDT/Y_USDT", m, methodology_version=METHODOLOGY_VERSION,
                                protocol_hash=CANONICAL.config_hash(), data_version="c" * 64)
        PairsTradeLogger().log_signal_rejection("X_USDT/Y_USDT", m, methodology_version=METHODOLOGY_VERSION,
                                                protocol_hash=CANONICAL.config_hash(), data_version="c" * 64)   # restart re-log
        rows = SignalRejection.objects.filter(pair_name="X_USDT/Y_USDT")
        self.assertEqual(rows.count(), 1)
        r = rows.get()
        self.assertEqual((r.intended_direction, r.reason, r.zscore, r.held_spread, r.methodology_version),
                         (lc.LONG, lc.ENTRY_SIGN_MISMATCH, -2.5, 0.03, METHODOLOGY_VERSION))
        self.assertEqual(r.data_version, "c" * 64)
        self.assertEqual(TradeRecord.objects.count(), 0)          # never a trade


# ── runner paths (offline exchange stub, stub execution) ────────────────────
T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
BETA_E, ALPHA_E = 1.3, 0.2


def _pa(h, pb):
    return math.exp(h + ALPHA_E + BETA_E * math.log(pb))


class _Exchange:
    def __init__(self):
        self.k = None

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=3):
        ts = int((T0 + self.k * HR).value // 1_000_000)
        pb = 10.0
        c = _pa(self.h, pb) if symbol == "X/USDT" else pb
        return [[ts - 3_600_000, c, c, c, c, 1], [ts, c, c, c, c, 1], [ts + 3_600_000, c, c, c, c, 1]]


class _Exec:
    def __init__(self):
        self.trade_logger = PairsTradeLogger()
        self.opened, self.closed, self.open_pos = [], [], False

    def is_pair_tradeable(self, name):
        return True

    def has_open_position(self, name):
        return self.open_pos

    def open_pair_trade(self, cfg, decision):
        self.opened.append(decision)
        self.open_pos = True
        return uuid.uuid4()

    def close_pair_trade(self, cfg, exit_reason, **kw):
        self.closed.append((exit_reason, kw))
        self.open_pos = False
        return True


class RunnerProvenancePathTests(TestCase):
    def _session(self, z, h_t):
        cfg = _cfg(BETA_E, ALPHA_E, 0.6, 10_000.0)
        eng = PairSignalEngine(cfg)
        t = T0 + 10 * HR
        eng._last_ts, eng._last_observed = t, (t, _pa(h_t, 10.0), 10.0)
        eng._prev_native = NativeStepResult(
            beta_pred=BETA_E, alpha_pred=ALPHA_E, spread=0.0, prediction_error=0.0, beta_uncertainty=0.0,
            is_warmup=False, zscore=z, pair_signal=-z, zscore_lag1=None, beta_post=BETA_E, alpha_post=ALPHA_E,
            observed=True, state_valid=True, segment=0, timestamp=t)
        sess = runner.PairSession(pair_config=cfg, engine=eng)
        sess.last_candle_ts = int(t.value // 1_000_000)
        sess.seed_ok, sess.seed = True, mock.Mock(data_version="d" * 64)
        return sess

    def _step(self, sess, ex, exe, k, h):
        ex.k, ex.h = k, h
        with mock.patch.object(runner, "evaluate_pair_health"), mock.patch.object(runner, "_entry_refusal_reason", return_value=None):
            return runner.process_pair_candle(sess, ex, exe)

    def test_mismatch_is_journaled_logged_by_reason_and_never_executed(self):
        sess, ex, exe = self._session(-2.5, +0.03), _Exchange(), _Exec()
        with self.assertLogs(runner.__name__, level="INFO") as logs:
            self._step(sess, ex, exe, 11, +0.03)
        line = [l for l in logs.output if "action=NONE" in l][0]
        self.assertIn("reason=ENTRY_SIGN_MISMATCH", line)
        self.assertIn("intended=LONG_SPREAD", line)
        self.assertEqual(exe.opened, [])
        self.assertEqual(SignalRejection.objects.get().data_version, "d" * 64)

    def test_ineligible_entry_is_refused_and_the_engine_holds_no_phantom(self):
        sess, ex, exe = self._session(-2.5, -0.03), _Exchange(), _Exec()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(runner, "ELIGIBILITY_DIR", d):
            self._step(sess, ex, exe, 11, -0.03)               # no canonical verdict exists
        self.assertEqual(exe.opened, [])
        self.assertIsNone(sess.engine.open_trade)

    def test_eligible_entry_then_reconciliation_affected_exit_is_passed_through(self):
        sess, ex, exe = self._session(-2.5, -0.03), _Exchange(), _Exec()
        with mock.patch.object(runner, "check_entry_eligibility", return_value=mock.Mock(ok=True)):
            self._step(sess, ex, exe, 11, -0.03)               # ENTER (canonically eligible by stub)
            self.assertEqual(len(exe.opened), 1)
            self._step(sess, ex, exe, 12, -0.03)
            self._step(sess, ex, exe, 14, +0.01)               # candle 13 missed → reconciliation; exit decided on 12? no: h<0 → hold
            self.assertTrue(sess.engine.requires_reconciliation)
            self._step(sess, ex, exe, 15, +0.01)               # cutoff 14 has h ≥ 0 → EXIT at 15, during reconciliation
        reason, kw = exe.closed[0]
        self.assertEqual(reason, lc.NORMALISATION)
        self.assertTrue(kw["reconciliation_affected"])
        self.assertIn("13:00:00", kw["reconciliation_gaps"])

    def test_clean_exit_is_not_marked(self):
        sess, ex, exe = self._session(-2.5, -0.03), _Exchange(), _Exec()
        with mock.patch.object(runner, "check_entry_eligibility", return_value=mock.Mock(ok=True)):
            self._step(sess, ex, exe, 11, -0.03)
            self._step(sess, ex, exe, 12, +0.01)
            self._step(sess, ex, exe, 13, +0.01)
        self.assertFalse(exe.closed[0][1]["reconciliation_affected"])
        self.assertEqual(exe.closed[0][1]["reconciliation_gaps"], "")


class ParityConsumerDistinguishesTests(SimpleTestCase):
    def test_clean_trade_pairs_excludes_flagged_rows(self):
        from bot.research.parity_forensics import ForensicReport
        tp = pd.DataFrame({"live_fill": [T0, T0, T0, None], "live_reconciliation_affected": [False, True, False, False],
                           "research_data_complete": [True, True, False, True],
                           "live_exit_fill_candle_missing": [False, False, False, False]})
        r = ForensicReport([], {}, 1, pd.DataFrame(), [], [], tp, pd.DataFrame(), {})
        self.assertEqual(list(r.clean_trade_pairs().index), [0])


# claude code changed: 2026-10-05 — provenance for the non-canonical research engines (cross-section, feature
# validation, contagion, PCA): every output records its exact inputs, config, code state and a run id; LEGACY only.
class ResearchEngineProvenanceTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _csv(self, name, closes):
        idx = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
        p = self.d / name
        pd.DataFrame({"timestamp": idx, "open": closes, "high": closes, "low": closes, "close": closes,
                      "volume": 1.0}).to_csv(p, index=False)
        return p

    def test_input_fingerprint_is_exact(self):
        from bot.quant_core import provenance as pv
        a, b = self._csv("A.csv", [1.0, 2.0]), self._csv("B.csv", [3.0, 4.0])
        f1, f2 = pv.input_files_fingerprint([a, b]), pv.input_files_fingerprint([b, a])
        self.assertEqual(f1["version"], f2["version"])                         # order of listing is not data
        self.assertEqual(set(f1["files"]), {a.as_posix(), b.as_posix()})
        self._csv("B.csv", [3.0, 4.000001])                                     # one value changes
        self.assertNotEqual(pv.input_files_fingerprint([a, b])["version"], f1["version"])
        fr = {"A": pd.DataFrame({"close": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC"))}
        v1 = pv.input_frames_fingerprint(fr, columns=["close", "volume"])     # absent column skipped, recorded
        self.assertEqual(v1["columns"], ["close"])
        fr["A"].iloc[1, 0] = 2.5
        self.assertNotEqual(pv.input_frames_fingerprint(fr, columns=["close"])["version"], v1["version"])

    def test_sidecar_is_legacy_complete_deterministic_and_never_canonical(self):
        from bot.quant_core import provenance as pv
        art, src = self._csv("OUT.csv", [1.0]), self._csv("IN.csv", [5.0])
        inputs, code = pv.input_files_fingerprint([src]), pv.code_state()
        pv.write_research_output_provenance(art, kind="CROSS_SECTION_FEATURES", producer="t", inputs=inputs,
                                            config={"k": 1}, code=code)
        p1 = pv.read_provenance(art)
        self.assertEqual((p1.status, p1.methodology_version, p1.data_version), (pv.LEGACY, pv.RESEARCH_ENGINE_METHODOLOGY, inputs["version"]))
        self.assertEqual(p1.extra["inputs"]["files"], inputs["files"])
        self.assertEqual(p1.artifact_sha256, pv.file_sha256(art))
        pv.write_research_output_provenance(art, kind="CROSS_SECTION_FEATURES", producer="t", inputs=inputs,
                                            config={"k": 1}, code=code)
        self.assertEqual(pv.read_provenance(art).run_id, p1.run_id)            # deterministic
        pv.write_research_output_provenance(art, kind="CROSS_SECTION_FEATURES", producer="t", inputs=inputs,
                                            config={"k": 2}, code=code)
        self.assertNotEqual(pv.read_provenance(art).run_id, p1.run_id)         # config is part of the identity
        r = pv.validate_canonical_provenance(p1, kind="CROSS_SECTION_FEATURES", methodology_version="STAGE2D-REVB-4",
                                             protocol_hash="x", artifact=art)
        self.assertFalse(r.ok)                                                 # never canonical

    def test_failure_is_logged_not_raised(self):
        from bot.quant_core import provenance as pv
        with self.assertLogs("bot.quant_core.provenance", level="WARNING"):
            self.assertIsNone(pv.write_research_output_provenance(self.d / "missing.csv", kind="X", producer="t", inputs=None))

    def test_cross_section_run_writes_a_sidecar_naming_its_exact_inputs(self):
        from bot.quant_core import provenance as pv
        from bot.research.cross_section_engine import UNIVERSE, run_cross_section_research
        rng = np.random.default_rng(0)
        data_dir, out_dir = self.d / "data", self.d / "out"
        data_dir.mkdir()
        used = []
        for sym in UNIVERSE[:4]:
            c = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(400)))
            p = data_dir / f"{sym}_1h.csv"
            idx = pd.date_range("2024-01-01", periods=400, freq="h", tz="UTC")
            pd.DataFrame({"timestamp": idx, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                          "volume": 10.0}).to_csv(p, index=False)
            used.append(p)
        with contextlib.redirect_stdout(io.StringIO()):
            run_cross_section_research(data_dir=str(data_dir), output_dir=str(out_dir))
        outs = sorted(out_dir.glob("*_cross_section.csv"))
        self.assertTrue(outs)
        expected = pv.input_files_fingerprint(used)
        for o in outs:
            prov = pv.read_provenance(o)
            self.assertEqual(prov.kind, "CROSS_SECTION_FEATURES")
            self.assertEqual(prov.data_version, expected["version"])
            self.assertEqual(set(prov.extra["inputs"]["files"]), set(expected["files"]))
            self.assertEqual(prov.artifact_sha256, pv.file_sha256(o))

    def test_pca_outputs_carry_sidecars(self):
        from bot.quant_core import provenance as pv
        from bot.research.pca_engine import save_outputs
        src = self._csv("BTC_USDT_1h.csv", [1.0, 2.0])
        inputs = pv.input_files_fingerprint([src])
        idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
        z = pd.DataFrame({"BTC_USDT": [0.1, 0.2, 0.3], "ETH_USDT": [0.3, 0.2, 0.1]}, index=idx)
        out = self.d / "pca"
        with contextlib.redirect_stdout(io.StringIO()):
            save_outputs(z, np.array([2.0, 1.0]), np.ones((2, 1)), ["BTC_USDT", "ETH_USDT"], 1,   # loadings: n_symbols x n_retained
                         pd.DataFrame({"symbol": ["BTC_USDT"], "ic": [0.0]}), output_dir=out, inputs=inputs)
        for name in ("pca_residuals.csv", "pca_components.csv", "pca_variance_explained.csv", "pca_ic_validation.csv"):
            prov = pv.read_provenance(out / name)
            self.assertEqual((prov.status, prov.data_version), (pv.LEGACY, inputs["version"]))
            self.assertTrue(prov.run_id)
