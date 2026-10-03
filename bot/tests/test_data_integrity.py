# claude code changed: new file — 2026-10-03, remediation F-09 (partial last candle).
import os
import shutil
import tempfile
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from bot import data_fetcher
from bot.research import data_integrity as di

ROWS = ["timestamp,open,high,low,close,volume", "2026-09-24 21:00:00+00:00,1,1,1,1,1",
        "2026-09-24 22:00:00+00:00,2,2,2,2,2", "2026-09-24 23:00:00+00:00,3,3,3,3,3"]


def _ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


class PartialCandleDetectionTests(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "X_USDT_1h.csv")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, eol="\n", trailing=True):
        with open(self.p, "wb") as f:
            f.write((eol.join(ROWS) + (eol if trailing else "")).encode())

    def test_boundary_classification(self):
        self._write()
        self.assertEqual(di.last_candle_status(self.p, mtime=_ts("2026-09-24 23:31:00"))["status"], "PARTIAL")
        self.assertEqual(di.last_candle_status(self.p, mtime=_ts("2026-09-24 23:59:59"))["status"], "PARTIAL")
        self.assertEqual(di.last_candle_status(self.p, mtime=_ts("2026-09-25 00:00:30"))["status"], "UNVERIFIABLE")
        self.assertEqual(di.last_candle_status(self.p, mtime=_ts("2026-09-25 00:01:00"))["status"], "COMPLETE")

    def test_correction_drops_only_the_last_row_byte_exact(self):
        for eol, trailing in (("\n", True), ("\r\n", True), ("\n", False), ("\r\n", False)):
            with self.subTest(eol=repr(eol), trailing=trailing):
                self._write(eol, trailing)
                os.utime(self.p, (_ts("2026-09-24 23:20:00"),) * 2)
                original = open(self.p, "rb").read()
                arch = os.path.join(self.d, "archive")
                out = di.build_corrected_version(os.path.join(self.d, "m.json"), paths=[self.p], archive_dir=arch)
                fixed = open(self.p, "rb").read()
                self.assertTrue(original.startswith(fixed))
                self.assertEqual(fixed, (eol.join(ROWS[:-1]) + eol).encode())     # last row gone, rest byte-identical
                self.assertEqual(out["files"][0]["dropped_row"], ROWS[-1])
                self.assertEqual(open(out["files"][0]["archived_v1"], "rb").read(), original)
                shutil.rmtree(arch)

    def test_complete_file_is_untouched(self):
        self._write()
        os.utime(self.p, (_ts("2026-09-25 06:00:00"),) * 2)
        before = open(self.p, "rb").read()
        out = di.build_corrected_version(os.path.join(self.d, "m.json"), paths=[self.p], archive_dir=os.path.join(self.d, "a"))
        self.assertEqual(open(self.p, "rb").read(), before)
        self.assertEqual(out["files"][0]["action"], "UNCHANGED")


class FetchTimeCompletedCandleRuleTests(SimpleTestCase):
    def _raw(self, open_ms):
        return [open_ms, "1", "1", "1", "1", "1", open_ms + 3_600_000 - 1, "1", 1, "1", "1", "0"]

    def test_forming_candle_is_dropped_at_fetch(self):
        t0 = 1_758_754_800_000                                    # 2025-09-24 23:00 UTC
        raw = [self._raw(t0 - 3_600_000), self._raw(t0)]           # last one closes at t0 + 1h − 1 ms
        df = data_fetcher._build_ohlcv_dataframe(raw, as_of_ms=t0 + 1_800_000)   # fetched mid-candle
        self.assertEqual(len(df), 1)
        df = data_fetcher._build_ohlcv_dataframe(raw, as_of_ms=t0 + 3_600_000)   # fetched exactly at the close
        self.assertEqual(len(df), 2)
        df = data_fetcher._build_ohlcv_dataframe(raw, as_of_ms=t0 + 3_600_000 - 1)  # 1 ms before the close
        self.assertEqual(len(df), 1)
