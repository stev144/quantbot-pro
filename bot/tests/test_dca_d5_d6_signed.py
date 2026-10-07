# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_dca_d5_d6_signed.py
# claude code changed: new file — 2026-10-07, owner sign-off of DECISION_SHEET_D5_D6_DCA_PROPOSAL_2026-10-07.md.
# D-5: the matched-exposure benchmark (known answer) and the excess statistic; D-6: closed-candle trailing volume
# (look-ahead fix), signed exclusions, point-in-time top-N, archive parsing (ms and µs timestamps), and the seal.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import dca_significance_d5 as d5
from bot.research import point_in_time_universe as pit


def T(s):
    return pd.Timestamp(s, tz="UTC")


class BenchmarkTests(SimpleTestCase):

    def test_matched_exposure_known_answer(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(direction="LONG", fee_rate=0.001, slippage_rate=0.0005)
        df = pd.DataFrame({"open": [100.0, 101.0, 102.0, 103.0], "close": [101.0, 102.0, 103.0, 110.0]})
        eq = pd.DataFrame({"capital_deployed": [0.0, 100.0, 100.0, 0.0]})
        b = d5.matched_exposure_benchmark_mtm(df, cfg, eq)
        # avg capital 50; entry 100·1.0005 = 100.05; exit 110·0.9995 = 109.945; units = 50/100.05
        # gross = units·9.895 = 4.945027486…; fees = (50 + units·109.945)·0.001 = 0.104947526…
        self.assertEqual(b["avg_deployed_capital"], 50.0)
        self.assertAlmostEqual(b["mtm"], 50 / 100.05 * 9.895 - (50 + 50 / 100.05 * 109.945) * 0.001, places=12)
        self.assertAlmostEqual(b["mtm"], 4.840080, places=5)

    def test_no_exposure_means_no_benchmark_pnl(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(direction="LONG", fee_rate=0.001, slippage_rate=0.0005)
        df = pd.DataFrame({"open": [1.0, 2.0], "close": [2.0, 3.0]})
        self.assertEqual(d5.matched_exposure_benchmark_mtm(df, cfg, pd.DataFrame({"capital_deployed": [0.0, 0.0]}))["mtm"], 0.0)

    def test_excess_statistic_is_dca_minus_benchmark_over_max_capital(self):
        from bot.research import dca_engine as de
        idx = pd.date_range("2022-01-01", periods=600, freq="h", tz="UTC")
        rng = np.random.default_rng(2)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600)))
        o = np.r_[c[0], c[:-1]]
        df = pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c), "low": np.minimum(o, c)}, index=idx)
        cfg = de.build_config_with_real_costs(de.BASELINE_CONFIG, "CRYPTO", "BTC/USDT")
        r = d5.d5_excess_statistic(df, cfg)
        self.assertAlmostEqual(r["statistic"], (r["dca_mtm"] - r["benchmark_mtm"]) / r["max_deployed_capital"], places=12)
        self.assertEqual((d5.MEAN_BLOCK_H, d5.SENSITIVITY_BLOCKS_H), (24, (6, 72)))


class TrailingVolumeLookAheadTests(SimpleTestCase):

    def test_the_candle_opening_at_t_is_not_known_at_t(self):
        idx = pd.date_range("2021-09-01", "2021-11-30", freq="D", tz="UTC")
        v = pd.Series(1.0, index=idx)
        t = T("2021-11-07")
        base = pit.trailing_volume_at(v, t)
        self.assertEqual(base, 30.0)                                         # Oct 8 … Nov 6 (30 closed daily candles)
        v2 = v.copy()
        v2[t] = 1e9                                                          # only the candle that OPENS at t changes
        self.assertEqual(pit.trailing_volume_at(v2, t), base)                # (the old rule counted it: look-ahead)


class UniverseRuleTests(SimpleTestCase):

    def test_signed_exclusions(self):
        bases = {"BTC", "ETH", "J", "SUPER"}
        self.assertEqual(pit.is_excluded_base("USDCUSDT", bases), "STABLECOIN_FIAT_OR_PEGGED")
        self.assertEqual(pit.is_excluded_base("EURUSDT", bases), "STABLECOIN_FIAT_OR_PEGGED")
        self.assertEqual(pit.is_excluded_base("PAXGUSDT", bases), "STABLECOIN_FIAT_OR_PEGGED")
        self.assertEqual(pit.is_excluded_base("BTCUPUSDT", bases), "LEVERAGED_TOKEN")
        self.assertEqual(pit.is_excluded_base("ETHBEARUSDT", bases), "LEVERAGED_TOKEN")
        self.assertEqual(pit.is_excluded_base("SUPERUSDT", bases), "")       # ends in neither suffix's base
        self.assertEqual(pit.is_excluded_base("JUPUSDT", {"BTC"}), "")        # 'J' is not a listed base → not leveraged
        self.assertEqual(pit.is_excluded_base("BTCUSDT", bases), "")

    def test_top_n_uses_only_information_at_t_and_includes_later_delistings(self):
        days = pd.date_range("2021-06-01", "2021-12-31", freq="D", tz="UTC")
        t = T("2021-11-07")
        lst = [pit.Listing("AAAUSDT", T("2021-06-01"), None), pit.Listing("BBBUSDT", T("2021-06-01"), T("2022-05-10")),
               pit.Listing("CCCUSDT", T("2021-09-01"), None), pit.Listing("DDDUSDT", T("2021-06-01"), T("2021-10-01"))]
        vols = {"AAAUSDT": pd.Series(5.0, index=days), "BBBUSDT": pd.Series(9.0, index=days),
                "CCCUSDT": pd.Series(99.0, index=days), "DDDUSDT": pd.Series(50.0, index=days)}
        # CCC listed < 90 days before t; DDD delisted before t; BBB (delisted LATER) is included and ranks first
        self.assertEqual(pit.top_n_universe_at(lst, t, vols, 5), ["BBBUSDT", "AAAUSDT"])
        future = {k: v.copy() for k, v in vols.items()}
        future["AAAUSDT"][future["AAAUSDT"].index >= t] = 1e9                  # change only the future
        self.assertEqual(pit.top_n_universe_at(lst, t, future, 5), ["BBBUSDT", "AAAUSDT"])
        self.assertEqual(pit.top_n_universe_at(lst, t, vols, 1), ["BBBUSDT"])
        self.assertEqual(pit.top_n_universe_at(lst, t, vols, 5, excluded={"BBBUSDT": "X"}), ["AAAUSDT"])


class ArchiveParsingTests(SimpleTestCase):

    def test_millisecond_and_microsecond_files(self):
        ms = "1633046400000,43820.01,48495.0,43283.03,48141.61,66244.87,1633132799999,3076094886.59,1771237,34156.88,1585000000.0,0\n"
        us = "1740787200000000,84349.95,84628.93,83824.78,83857.92,636.60,1740790799999999,53599194.44,131136,249.06,20981000.0,0\n"
        a, b = pit.parse_kline_csv(ms), pit.parse_kline_csv(us)
        self.assertEqual(a.index[0], T("2021-10-01"))
        self.assertEqual(b.index[0], T("2025-03-01"))
        self.assertEqual(a["quote_volume"].iloc[0], 3076094886.59)

    def test_sealed_months_are_never_requested(self):
        from bot.quant_core.research_zones import HoldoutSealedError
        pit._refuse_sealed_month("2026-09")                                   # last month allowed
        with self.assertRaises(HoldoutSealedError):
            pit._refuse_sealed_month("2026-10")
        with self.assertRaises(HoldoutSealedError):
            pit.fetch_month("BTCUSDT", "1h", "2027-01")                       # refused before any network call
