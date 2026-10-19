# claude code changed: new file — 2026-10-03, owner decision D-6 (point-in-time universe).
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import point_in_time_universe as pit

T = lambda s: pd.Timestamp(s, tz="UTC")


def _vol(start="2021-01-01", days=900, per_hour=1000.0):
    idx = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    return pd.Series(per_hour, index=idx)


class MembershipTests(SimpleTestCase):
    def setUp(self):
        self.live = pit.Listing("AAA/USDT", T("2022-01-01"), None)
        self.dead = pit.Listing("DDD/USDT", T("2021-01-01"), T("2023-06-30 23:00"), "binance-delisting-2023-06")

    def test_listing_age_boundary_is_90_days(self):
        self.assertFalse(pit.is_member(self.live, T("2022-04-01") - pd.Timedelta(hours=1), 1e9, min_volume_usdt=0))
        self.assertTrue(pit.is_member(self.live, T("2022-04-01"), 1e9, min_volume_usdt=0))      # 90 days exactly

    def test_delisted_symbol_is_member_before_and_not_after_its_last_kline(self):
        self.assertTrue(pit.is_member(self.dead, T("2023-06-30 23:00"), 1e9, min_volume_usdt=0))
        self.assertFalse(pit.is_member(self.dead, T("2023-07-01"), 1e9, min_volume_usdt=0))

    def test_volume_floor_is_required_and_missing_volume_is_not_membership(self):
        with self.assertRaisesRegex(ValueError, "owner parameter"):
            pit.is_member(self.live, T("2023-01-01"), 1e9, min_volume_usdt=None)
        self.assertFalse(pit.is_member(self.live, T("2023-01-01"), None, min_volume_usdt=0))
        self.assertFalse(pit.is_member(self.live, T("2023-01-01"), 99.0, min_volume_usdt=100.0))


class TrailingVolumeTests(SimpleTestCase):
    def test_window_is_the_30_days_ending_at_t_and_never_extrapolated(self):
        v = _vol()
        self.assertEqual(pit.trailing_volume_at(v, T("2022-01-31 00:00")), 1000.0 * 24 * 30)
        self.assertIsNone(pit.trailing_volume_at(v, T("2021-01-15")))          # window not fully covered

    def test_future_volume_cannot_change_past_membership(self):
        v = _vol()
        v2 = v.copy()
        v2[v2.index > T("2022-06-01")] = 0.0                                      # change only the future
        lst = [pit.Listing("AAA/USDT", T("2021-01-01"), None)]
        t = T("2022-06-01")
        self.assertEqual(pit.universe_at(lst, t, {"AAA/USDT": v}, min_volume_usdt=1.0),
                         pit.universe_at(lst, t, {"AAA/USDT": v2}, min_volume_usdt=1.0))


class VersionAndTableTests(SimpleTestCase):
    def test_universe_version_changes_with_the_floor_and_the_listings(self):
        lst = [pit.Listing("AAA/USDT", T("2021-01-01"), None)]
        a = pit.universe_version(lst, min_volume_usdt=1e6)
        self.assertNotEqual(a, pit.universe_version(lst, min_volume_usdt=2e6))
        self.assertNotEqual(a, pit.universe_version(lst + [pit.Listing("BBB/USDT", T("2021-02-01"), None)], min_volume_usdt=1e6))

    def test_missing_listing_table_blocks_with_an_explanation(self):
        import tempfile
        from pathlib import Path
        with self.assertRaisesRegex(FileNotFoundError, "BLOCKED"):
            pit.load_listing_table(Path(tempfile.gettempdir()) / "no_such_pit_table.json")
        # claude code changed: 2026-10-07 — build_listing_table is now implemented (owner sign-off of
        # DECISION_SHEET_D5_D6_DCA_PROPOSAL_2026-10-07.md item 3 authorised the archive download); it needs the network,
        # so tests no longer call it. What must still hold: without a table, research stays BLOCKED (asserted above).
