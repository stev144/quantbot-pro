# bot/tests/test_pairs_orientation.py
"""Pair-orientation lookup in bot/pairs/config.py (2026-10-21).   # claude code changed: new

Engle-Granger is not symmetric, so a pair stored reversed must never be read back with an inverted hedge ratio.
Real temporary CSV files, no mocks.
"""
import csv
import os
import tempfile
import unittest

from bot.pairs.config import _load_cointegration_row

FIELDS = ["pair_name", "symbol_a", "symbol_b", "hedge_ratio", "intercept", "passes_filters", "reject_reason"]


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


class OrientationLookupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.scan = os.path.join(self.dir, "scan.csv")
        self.supp = os.path.join(self.dir, "supp.csv")
        _write(self.scan, [
            {"pair_name": "A_USDT/B_USDT", "symbol_a": "A_USDT", "symbol_b": "B_USDT", "hedge_ratio": "0.5",
             "intercept": "1.0", "passes_filters": "True", "reject_reason": ""},
            {"pair_name": "D_USDT/C_USDT", "symbol_a": "D_USDT", "symbol_b": "C_USDT", "hedge_ratio": "1.25",
             "intercept": "-0.3", "passes_filters": "True", "reject_reason": ""},
        ])

    def test_forward_row_is_returned_unchanged(self):
        row = _load_cointegration_row("A_USDT/B_USDT", csv_path=self.scan, supplement_path=self.supp)
        self.assertEqual(row["hedge_ratio"], "0.5")

    def test_reversed_only_raises_and_never_inverts(self):
        with self.assertRaisesRegex(ValueError, "REVERSED"):
            _load_cointegration_row("C_USDT/D_USDT", csv_path=self.scan, supplement_path=self.supp)

    def test_supplement_row_is_used_for_the_configured_orientation(self):
        _write(self.supp, [{"pair_name": "C_USDT/D_USDT", "symbol_a": "C_USDT", "symbol_b": "D_USDT",
                            "hedge_ratio": "0.77", "intercept": "0.2", "passes_filters": "False",
                            "reject_reason": "orientation supplement — monitoring only"}])
        row = _load_cointegration_row("C_USDT/D_USDT", csv_path=self.scan, supplement_path=self.supp)
        self.assertEqual(row["hedge_ratio"], "0.77")                 # its own regression, not 1 / 1.25
        self.assertEqual(row["passes_filters"], "False")             # never entry-eligible from a supplement

    def test_scan_row_wins_over_supplement(self):
        _write(self.supp, [{"pair_name": "A_USDT/B_USDT", "symbol_a": "A_USDT", "symbol_b": "B_USDT",
                            "hedge_ratio": "9.9", "intercept": "0", "passes_filters": "False", "reject_reason": "x"}])
        row = _load_cointegration_row("A_USDT/B_USDT", csv_path=self.scan, supplement_path=self.supp)
        self.assertEqual(row["hedge_ratio"], "0.5")

    def test_absent_pair_raises_not_found(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            _load_cointegration_row("X_USDT/Y_USDT", csv_path=self.scan, supplement_path=self.supp)


if __name__ == "__main__":
    unittest.main()
