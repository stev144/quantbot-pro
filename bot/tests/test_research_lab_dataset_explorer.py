# ============================================================
# bot/tests/test_research_lab_dataset_explorer.py
# claude code changed: new file — regression test for a real production
# bug: GET /research/ returned a 500 (IndexError: invalid index to
# scalar variable) whenever a data/*.csv or research_data/observations.csv
# had a "timestamp" column pandas inferred as numeric rather than string.
# get_dataset_explorer() was passing df["timestamp"].iloc[0]/.min()/.max()
# straight into the template context, which does {{ ...|slice:":10" }}
# assuming an ISO date STRING — slicing a numpy scalar (np.int64/np.float64)
# raises IndexError, which Django's own slice filter does NOT catch (it
# only swallows ValueError/TypeError/KeyError per
# django/template/defaultfilters.py's slice_filter), so the exception
# propagated and crashed the whole page. Fixed by wrapping both values in
# str(...) at the source (bot/views/research_lab_data.py) rather than
# assuming the CSV's dtype.
# ============================================================

import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from bot.views import research_lab_data as rl


class DatasetExplorerNumericTimestampRegressionTest(SimpleTestCase):
    """
    Reproduces the exact real bug: a timestamp column pandas reads as
    numeric (e.g. epoch-style integers) rather than an ISO date string.
    """

    def setUp(self):
        self.tmp_data_dir = tempfile.mkdtemp()
        self.tmp_research_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_data_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.tmp_research_dir, ignore_errors=True)

    def _write_csv(self, path, header, rows):
        with open(path, "w") as f:
            f.write(header + "\n")
            for row in rows:
                f.write(row + "\n")

    def test_numeric_per_symbol_timestamp_does_not_crash_and_is_sliceable(self):
        # claude code changed: a plain integer "timestamp" column — no
        # quotes, no ISO format — is exactly what makes pandas infer int64
        # and is exactly the shape that crashed get_dataset_explorer()
        # before the fix.
        csv_path = os.path.join(self.tmp_data_dir, "BTC_USDT.csv")
        self._write_csv(csv_path, "timestamp", ["1700000000", "1700003600", "1700007200"])

        with mock.patch.object(rl, "DATA_DIR", self.tmp_data_dir), \
             mock.patch.object(rl, "RESEARCH_DATA_DIR", self.tmp_research_dir):
            result = rl.get_dataset_explorer()   # must not raise

        row = next(r for r in result["assets"] if r["symbol"] == "BTC_USDT")
        self.assertIsInstance(row["start"], str)
        self.assertIsInstance(row["end"], str)
        # the exact operation the template applies — must not raise here either
        self.assertEqual(row["start"][:10], "1700000000"[:10])

    def test_numeric_observations_timestamp_does_not_crash_and_is_sliceable(self):
        obs_path = os.path.join(self.tmp_research_dir, "observations.csv")
        self._write_csv(obs_path, "symbol,timestamp", ["BTC/USDT,1700000000", "ETH/USDT,1700003600"])

        with mock.patch.object(rl, "DATA_DIR", self.tmp_data_dir), \
             mock.patch.object(rl, "RESEARCH_DATA_DIR", self.tmp_research_dir):
            result = rl.get_dataset_explorer()   # must not raise

        observations = result["observations"]
        self.assertTrue(observations["available"])
        self.assertIsInstance(observations["start"], str)
        self.assertIsInstance(observations["end"], str)
        self.assertEqual(observations["start"][:10], observations["start"][:10])   # sliceable, no crash

    def test_normal_iso_string_timestamp_still_works(self):
        # claude code changed: the non-buggy, expected-shape case —
        # confirms the fix doesn't change behavior for well-formed data.
        csv_path = os.path.join(self.tmp_data_dir, "ETH_USDT.csv")
        self._write_csv(csv_path, "timestamp", ["2020-01-01 00:00:00+00:00", "2020-01-01 01:00:00+00:00"])

        with mock.patch.object(rl, "DATA_DIR", self.tmp_data_dir), \
             mock.patch.object(rl, "RESEARCH_DATA_DIR", self.tmp_research_dir):
            result = rl.get_dataset_explorer()

        row = next(r for r in result["assets"] if r["symbol"] == "ETH_USDT")
        self.assertEqual(row["start"][:10], "2020-01-01")

    def test_missing_observations_file_reports_unavailable_not_a_crash(self):
        with mock.patch.object(rl, "DATA_DIR", self.tmp_data_dir), \
             mock.patch.object(rl, "RESEARCH_DATA_DIR", self.tmp_research_dir):
            result = rl.get_dataset_explorer()
        self.assertFalse(result["observations"]["available"])
