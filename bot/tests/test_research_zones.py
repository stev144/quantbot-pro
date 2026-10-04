# claude code changed: new file — 2026-10-03, owner decision D-8 (research zones + sealed forward holdout).
import json
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core.protocol import CANONICAL
from bot.quant_core import research_zones as rz
from bot.quant_core.research_zones import ZONES, HoldoutSealedError, open_holdout_evaluation

T = lambda s: pd.Timestamp(s, tz="UTC")


class ZoneDefinitionTests(SimpleTestCase):
    def test_signed_boundaries_exactly(self):
        self.assertEqual(ZONES.S0, T("2021-11-07 00:00"))
        self.assertEqual(ZONES.S1, T("2024-03-31 23:00"))
        self.assertEqual(ZONES.V0, T("2024-04-22 00:00"))
        self.assertEqual(ZONES.V1, T("2026-09-24 23:00"))
        self.assertEqual(ZONES.F, T("2026-10-04 00:00"))
        self.assertEqual(ZONES.E, T("2027-04-04 00:00"))
        self.assertEqual((ZONES.extension_days, ZONES.min_holdout_trades), (90, 30))
        self.assertEqual(ZONES.init_window(), (T("2021-09-26 08:00"), T("2021-11-06 23:00")))

    def test_zones_and_protocol_agree(self):
        self.assertEqual(ZONES.init_window_candles, CANONICAL.require_init_window())
        self.assertEqual(CANONICAL.require_selection_start(), ZONES.S0)
        self.assertEqual(ZONES.V0 - ZONES.S1 - pd.Timedelta(hours=1), pd.Timedelta(hours=ZONES.embargo_candles))

    def test_every_boundary_candle_classifies_correctly(self):
        cases = [("2021-09-26 07:00", rz.PRE_INIT_UNUSED), ("2021-09-26 08:00", rz.INIT), ("2021-11-06 23:00", rz.INIT),
                 ("2021-11-07 00:00", rz.SELECTION), ("2024-03-31 23:00", rz.SELECTION), ("2024-04-01 00:00", rz.EMBARGO),
                 ("2024-04-21 23:00", rz.EMBARGO), ("2024-04-22 00:00", rz.VALIDATION), ("2026-09-24 23:00", rz.VALIDATION),
                 ("2026-09-25 00:00", rz.UNASSIGNED_EXPOSED), ("2026-10-03 23:00", rz.UNASSIGNED_EXPOSED),
                 ("2026-10-04 00:00", rz.FORWARD_HOLDOUT)]
        for ts, zone in cases:
            with self.subTest(ts=ts):
                self.assertEqual(ZONES.classify(ts), zone)
        self.assertTrue(ZONES.claim_eligible(rz.FORWARD_HOLDOUT))
        for z in (rz.SELECTION, rz.VALIDATION, rz.UNASSIGNED_EXPOSED, rz.INIT):
            self.assertFalse(ZONES.claim_eligible(z))

    def test_embargo_is_504_candles_and_no_overlap(self):
        idx = pd.date_range(ZONES.S0, ZONES.V1, freq="h")
        z = ZONES.classify_index(idx)
        self.assertEqual(int((z == rz.EMBARGO).sum()), 504)
        self.assertEqual(int((z == rz.SELECTION).sum()) + 504 + int((z == rz.VALIDATION).sum()), len(idx))

    def test_inconsistent_definitions_are_refused(self):
        for kw in ({"validation_start": "2024-04-21T00:00:00+00:00"},          # embargo too short
                   {"first_evaluation": "2027-04-05T00:00:00+00:00"},          # not F + 182 d
                   {"holdout_start": "2026-09-20T00:00:00+00:00"},             # F inside V
                   {"selection_start": "2021-11-07T00:30:00+00:00"}):          # off-grid
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                replace(ZONES, **kw)

    def test_any_boundary_change_changes_the_zones_hash(self):
        h = ZONES.zones_hash()
        moved = replace(ZONES, validation_end="2026-09-23T23:00:00+00:00")
        self.assertNotEqual(moved.zones_hash(), h)


class SealTests(SimpleTestCase):
    def test_sealed_candles_are_refused_and_pre_F_data_passes(self):
        ZONES.assert_no_sealed_data(pd.date_range("2026-09-01", "2026-10-03 23:00", freq="h", tz="UTC"), "test")
        with self.assertRaises(HoldoutSealedError):
            ZONES.assert_no_sealed_data(pd.date_range("2026-10-03 22:00", periods=3, freq="h", tz="UTC"), "test")

    def test_run_canonical_refuses_sealed_data(self):
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        idx = pd.date_range("2026-09-01", "2026-10-05", freq="h", tz="UTC")
        s = pd.Series(100.0, index=idx)
        eng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
        with self.assertRaises(HoldoutSealedError):
            eng.run_canonical(s, s * 1.1)

    def test_split_and_sealed_store_never_overwrite(self):
        d = Path(tempfile.mkdtemp())
        try:
            df = pd.DataFrame({"timestamp": pd.date_range("2026-10-03 22:00", periods=4, freq="h", tz="UTC"),
                               "close": [1.0, 2.0, 3.0, 4.0]})
            research, sealed = ZONES.split_sealed(df, ts_col="timestamp")
            self.assertEqual(len(research), 2)
            self.assertEqual(len(sealed), 2)
            p = rz.write_sealed_rows(sealed, "X_1h.csv", d)
            again = sealed.assign(close=[99.0, 99.0])                 # a re-fetch must not overwrite a sealed candle
            rz.write_sealed_rows(again, "X_1h.csv", d)
            stored = pd.read_csv(p)
            self.assertEqual(stored["close"].tolist(), [3.0, 4.0])
            self.assertIsNone(rz.write_sealed_rows(sealed.iloc[:0], "X_1h.csv", d))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EvaluateOnceLedgerTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ledger = self.d / "ledger.json"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_not_due_before_scheduled_date(self):
        with self.assertRaisesRegex(HoldoutSealedError, "not due"):
            open_holdout_evaluation("r0", now=datetime(2027, 4, 3, 23, tzinfo=timezone.utc), ledger_path=self.ledger)
        self.assertFalse(self.ledger.exists())                     # a refused attempt reads and writes nothing

    def test_too_few_trades_defers_90_days_then_evaluates_once(self):
        ev = open_holdout_evaluation("r1", now=datetime(2027, 4, 4, tzinfo=timezone.utc), n_trades=12, ledger_path=self.ledger)
        self.assertEqual((ev["type"], ev["next_due"]), ("DEFERRED", "2027-07-03T00:00:00+00:00"))
        with self.assertRaisesRegex(HoldoutSealedError, "not due"):
            open_holdout_evaluation("r2", now=datetime(2027, 6, 1, tzinfo=timezone.utc), n_trades=40, ledger_path=self.ledger)
        ev = open_holdout_evaluation("r3", now=datetime(2027, 7, 3, tzinfo=timezone.utc), n_trades=40, ledger_path=self.ledger)
        self.assertEqual(ev["type"], "EVALUATED")
        with self.assertRaisesRegex(HoldoutSealedError, "only once"):
            open_holdout_evaluation("r4", now=datetime(2028, 1, 1, tzinfo=timezone.utc), n_trades=100, ledger_path=self.ledger)
        events = json.loads(self.ledger.read_text())["events"]
        self.assertEqual([e["type"] for e in events], ["DEFERRED", "EVALUATED"])

    def test_ledger_of_a_different_zone_definition_is_refused(self):
        self.ledger.write_text(json.dumps({"zones_version": "X", "zones_hash": "deadbeef", "events": []}))
        with self.assertRaisesRegex(HoldoutSealedError, "different zones"):
            open_holdout_evaluation("r", now=datetime(2027, 5, 1, tzinfo=timezone.utc), ledger_path=self.ledger)


class EpochTimestampSealTests(SimpleTestCase):   # funding / OI files store epoch-ms timestamps
    def test_epoch_ms_and_seconds_are_sealed_correctly(self):
        f_ms = int(ZONES.F.value // 1_000_000)
        for scale, label in ((1, "ms"), (1000, "s")):
            df = pd.DataFrame({"timestamp": [(f_ms - 3_600_000) // scale, f_ms // scale], "x": [1, 2]})
            with self.subTest(unit=label):
                research, sealed = ZONES.split_sealed(df, ts_col="timestamp")
                self.assertEqual((len(research), len(sealed)), (1, 1))

    def test_seal_before_write_withholds_and_stores(self):
        d = Path(tempfile.mkdtemp())
        try:
            df = pd.DataFrame({"timestamp": pd.date_range("2026-10-03 23:00", periods=3, freq="h", tz="UTC"), "close": [1.0, 2.0, 3.0]})
            out = rz.seal_before_write(df, d / "X_1h.csv")
            self.assertEqual(len(out), 1)
            self.assertEqual(len(pd.read_csv(d / rz.SEALED_DIR_NAME / "X_1h.csv")), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)
