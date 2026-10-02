# claude code changed: new file — Phase 3 engineering (H-07, M-01, M-02, H-11).
# Every defect is reproduced as a test that FAILS on the pre-fix code and passes
# on the fix; expected values are hand arithmetic. The strategy layer is
# replaced by a scripted router (the real strategies are gated off and this is
# not strategy research), so the tests isolate execution and accounting.
# Synthetic OHLC; no network.

import math
from types import SimpleNamespace

import ccxt
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.backtesting import backtester as btm
from bot.backtesting.backtester import Backtester
from bot.core import bot_runner


# ── H-07: forming candle ──────────────────────────────────────────────────────
HOUR = 3_600_000


class FormingCandleTests(SimpleTestCase):
    def _candles(self, n, t0=1_700_000_000_000):
        return [[t0 + k * HOUR, 100.0, 101.0, 99.0, 100.0 + k, 1.0] for k in range(n)]

    def test_forming_candle_is_dropped_closed_ones_kept(self):
        c = self._candles(4)
        last_open = c[-1][0]
        self.assertEqual(len(bot_runner.drop_forming_candles(c, HOUR, last_open + HOUR // 2)), 3)   # mid-candle
        self.assertEqual(len(bot_runner.drop_forming_candles(c, HOUR, last_open + HOUR)), 4)        # exactly at its close
        self.assertEqual(len(bot_runner.drop_forming_candles(c, HOUR, last_open + HOUR - 1)), 3)    # 1 ms before close

    def test_invalid_timeframe_refused(self):
        with self.assertRaises(ValueError):
            bot_runner.drop_forming_candles(self._candles(2), 0, 10)

    def test_runner_decides_on_closed_candles_only(self):
        candles = self._candles(150)
        now = candles[-1][0] + HOUR // 3                                # the last candle is still forming
        seen = {}

        class Exch:
            id = "stub"
            parse_timeframe = staticmethod(ccxt.Exchange.parse_timeframe)
            def fetch_ohlcv(self, symbol, timeframe, limit):
                return [list(c) for c in candles]
            def milliseconds(self):
                return now

        def detect(df):
            seen["detector_last_close"] = float(df["close"].iloc[-1])
            return SimpleNamespace(regime="RANGING", confidence="LOW", adx=10.0, atr_ratio=1.0)

        def route(df, regime):
            seen["router_rows"] = len(df)
            return {"signal": "NO_SIGNAL", "reason": "test"}

        engine = SimpleNamespace(dry_run=True, manage_positions=lambda: None,
                                 position_tracker=SimpleNamespace(get_all_positions=lambda: {}))
        session = bot_runner.VenueSession(venue_id="stub", exchange=Exch(), symbol="X/USDT", engine=engine,
                                          detector=SimpleNamespace(detect=detect), router=SimpleNamespace(route=route),
                                          narrator=None)
        self.assertEqual(bot_runner.process_venue_candle(session), "ok")
        self.assertEqual(seen["router_rows"], 149)                        # the forming candle never reached the strategy
        self.assertEqual(seen["detector_last_close"], candles[-2][4])     # last CLOSED candle's close


# ── M-01 / M-02 backtester fixtures ───────────────────────────────────────────
class ScriptedRouter:
    """Emits one BUY at candle index `at` (a decision at that candle's close)."""
    def __init__(self, at, sl, tp):
        self.at, self.sl, self.tp = at, sl, tp
    def route(self, current_df, regime):
        if len(current_df) and current_df.index[-1] == self.ts:
            px = float(current_df["close"].iloc[-1])
            return {"signal": "BUY", "entry": px, "sl": self.sl, "tp": self.tp, "rsi": 50.0, "reason": "scripted",
                    "strategy": "Scripted"}
        return {"signal": "NO_SIGNAL", "reason": "scripted_idle"}


def _bt(rows, signal_at, sl, tp, fee=0.0, slip=0.0):
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    bt = Backtester(df=df, initial_balance=10_000.0)
    bt.fee_rate, bt.slippage_rate = fee, slip
    r = ScriptedRouter(signal_at, sl, tp)
    r.ts = idx[signal_at]
    bt.router = r
    return bt


FLAT = [(100.0, 100.5, 99.5, 100.0)] * 110


class FillMechanicsTests(SimpleTestCase):
    def test_entry_fills_at_next_open_not_the_signal_close(self):
        rows = FLAT + [(102.0, 103.0, 101.5, 102.5)] + [(102.5, 103.0, 102.0, 102.5)] * 5
        res = _bt(rows, 109, sl=90.0, tp=200.0).run()
        t = res["trades"][0]
        self.assertAlmostEqual(t["entry_price"], 102.0)                 # open of candle 110 (was: 100, the close of 109)
        self.assertEqual(res["fill_model"], btm.FILL_MODEL)

    def test_entry_pays_slippage_on_the_open(self):
        rows = FLAT + [(102.0, 103.0, 101.5, 102.5)] + [(102.5,) * 4] * 5
        res = _bt(rows, 109, sl=90.0, tp=200.0, slip=0.001).run()
        self.assertAlmostEqual(res["trades"][0]["entry_price"], 102.0 * 1.001, places=8)

    def test_gap_through_stop_fills_at_the_open(self):
        rows = FLAT + [(100.0, 100.5, 99.5, 100.0), (88.0, 89.0, 87.0, 88.5)] + [(88.5,) * 4] * 3
        t = _bt(rows, 109, sl=95.0, tp=120.0).run()["trades"][0]
        self.assertEqual(t["exit_reason"], "STOP LOSS")
        self.assertAlmostEqual(t["exit_price"], 88.0)                   # was 95.0 — the stop level, through a gap

    def test_gap_through_take_profit_fills_at_the_better_open(self):
        rows = FLAT + [(100.0, 100.5, 99.5, 100.0), (125.0, 126.0, 94.0, 125.0)] + [(125.0,) * 4] * 3
        t = _bt(rows, 109, sl=95.0, tp=120.0).run()["trades"][0]
        self.assertEqual(t["exit_reason"], "TAKE PROFIT")               # the open passed TP first, even though low < SL later
        self.assertAlmostEqual(t["exit_price"], 125.0)

    def test_both_levels_inside_the_range_assume_stop_first(self):
        rows = FLAT + [(100.0, 100.5, 99.5, 100.0), (100.0, 121.0, 94.0, 100.0)] + [(100.0,) * 4] * 3
        t = _bt(rows, 109, sl=95.0, tp=120.0).run()["trades"][0]
        self.assertEqual(t["exit_reason"], "STOP LOSS")
        self.assertAlmostEqual(t["exit_price"], 95.0)

    def test_signal_on_the_last_candle_is_never_filled(self):
        res = _bt(FLAT + [(100.0,) * 4], 110, sl=90.0, tp=120.0).run()
        self.assertEqual(res["total_trades"], 0)
        self.assertEqual(res["unfilled_signals_at_data_end"], 1)

    def test_future_candles_cannot_change_an_earlier_fill(self):
        tail = [(101.0, 101.5, 100.5, 101.0)] * 30
        a = _bt(FLAT + [(101.0,) * 4] + tail, 109, sl=90.0, tp=300.0).run()
        b = _bt(FLAT + [(101.0,) * 4] + [(c[0] * 1.3, c[1] * 1.3, c[2] * 1.3, c[3] * 1.3) for c in tail], 109, sl=90.0, tp=300.0).run()
        self.assertEqual(a["trades"][0]["entry_price"], b["trades"][0]["entry_price"])


class MarkToMarketDrawdownTests(SimpleTestCase):
    def test_intra_trade_loss_is_in_the_drawdown(self):
        # enter at 100 (open of 110); closes fall to 60 then recover to TP 110 — the closed trade WINS,
        # so the old closed-trade drawdown is 0; the real interim loss is qty × 40.
        rows = (FLAT + [(100.0, 100.5, 99.5, 100.0), (100.0, 100.0, 60.0, 60.0), (60.0, 60.0, 60.0, 60.0),
                        (60.0, 111.0, 60.0, 111.0)] + [(111.0,) * 4] * 3)
        res = _bt(rows, 109, sl=50.0, tp=110.0).run()
        t = res["trades"][0]
        self.assertEqual(t["exit_reason"], "TAKE PROFIT")
        self.assertAlmostEqual(t["exit_price"], 110.0)
        qty = t["profit"] / (110.0 - 100.0)                             # zero costs: TP profit = qty × 10
        expected_dd = qty * (100.0 - 60.0) / 10_000.0 * 100.0
        self.assertAlmostEqual(res["max_drawdown"], round(expected_dd, 2), places=2)
        self.assertEqual(res["max_drawdown_closed_trade_basis"], 0.0)
        self.assertGreater(res["max_drawdown"], 0.0)


# ── H-11: ratios ──────────────────────────────────────────────────────────────
class RatioTests(SimpleTestCase):
    def test_time_series_sharpe_known_answer(self):
        r = pd.Series([0.02, 0.0, 0.02, 0.0])
        # mean 0.01, sd (ddof=1) 0.011547005 → 0.8660254 × sqrt(365) (19.1049732) = 16.54539 → 16.5454
        self.assertAlmostEqual(btm.sharpe_from_returns(r, rf_annual=0.0), 16.5454, places=4)

    def test_risk_free_is_daily_compounded_and_subtracted(self):
        r = pd.Series([0.001, 0.002, 0.003, 0.004])
        rf_p = 1.05 ** (1 / 365) - 1
        ex = r - rf_p
        expected = round(ex.mean() / ex.std(ddof=1) * math.sqrt(365), 4)
        self.assertAlmostEqual(btm.sharpe_from_returns(r), expected, places=4)

    def test_time_series_sortino_textbook_downside_deviation(self):
        r = pd.Series([0.02, -0.01, 0.02, -0.01])
        # mean 0.005; DD = sqrt((0 + 1e-4 + 0 + 1e-4)/4) = 0.0070711 → 0.70711 × sqrt(365) = 13.5093
        self.assertAlmostEqual(btm.sortino_from_returns(r, rf_annual=0.0), 13.5093, places=4)

    def test_undefined_ratios_are_none_not_sentinels(self):
        self.assertIsNone(btm.sharpe_from_returns(pd.Series([0.01])))
        self.assertIsNone(btm.sharpe_from_returns(pd.Series([0.01, 0.01, 0.01]), rf_annual=0.0))
        self.assertIsNone(btm.sortino_from_returns(pd.Series([0.01, 0.02]), rf_annual=0.0))

    def test_trade_sharpe_has_no_unit_mismatched_risk_free_term(self):
        trades = [{"r_multiple": r} for r in (1.0, -1.0) * 5]
        self.assertEqual(btm.calculate_sharpe_ratio(trades, years_elapsed=1.0), 0.0)   # mean R = 0 → 0 (old code: −0.0487)

    def test_daily_returns_use_the_last_equity_of_each_day(self):
        idx = pd.to_datetime(["2025-01-01 05:00", "2025-01-01 23:00", "2025-01-02 10:00", "2025-01-02 23:00"], utc=True)
        r = btm.daily_mtm_returns(pd.Series([100.0, 110.0, 50.0, 121.0], index=idx))
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r.iloc[0], 0.1)                          # 121 / 110 − 1 (intraday 50 is not a daily close)

    def test_backtester_reports_its_bases(self):
        res = _bt(FLAT + [(101.0,) * 4] * 60, 109, sl=90.0, tp=300.0).run()
        self.assertEqual(res["ratio_basis"], btm.RATIO_BASIS)
        self.assertEqual(res["drawdown_basis"], btm.DRAWDOWN_BASIS)
        self.assertIn("r_sharpe_trade_based", res)
        self.assertIn("daily_return_lag1_autocorr", res)
