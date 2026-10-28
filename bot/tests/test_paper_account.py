# bot/tests/test_paper_account.py  # claude code changed: 2026-10-28 — new: account-aware Paper Trader (fixed deposit, close-out)
"""Paper Trader on a fixed account: the summary mapping (known answers) and one real-data replay."""
import pandas as pd
from django.test import SimpleTestCase

from bot.paper_trading import ranging_paper_engine as pe
from bot.research import fx_survival_replay as sr

T0 = pd.Timestamp("2025-01-06 00:00", tz="UTC")
LABELS = {"spread": "S", "slippage": "SL", "commission": "C", "swap": "W", "liquidation_fee": "L"}


def _cycle(i, fills, net, outcome=None):
    return {"cycle_id": i, "entry_ts": T0 + pd.Timedelta(hours=10 * i), "exit_ts": T0 + pd.Timedelta(hours=10 * i + 5),
            "entry_price": 1.0, "exit_exec_price": 1.01, "wap": 1.0, "fills": fills, "net_pnl_usd": net,
            "gross_pnl_usd": net + 1.0, "spread_usd": 1.0, "swap_usd": 0.0, "commission_usd": 0.0, "duration_h": 5.0,
            "exit_reason": "X", "outcome": outcome or (sr.PROFITABLE_EXIT if net > 0 else sr.LOSING_EXIT)}


def _res(equity, worst=None, cycles=(), events=(), insolvent=False, start=1000.0):
    ts = pd.date_range(T0, periods=len(equity), freq="h")
    rows = pd.DataFrame({"timestamp": ts, "balance": equity, "equity": equity,
                         "equity_intrabar_worst": worst if worst is not None else equity})
    return {"rows": rows, "cycles": pd.DataFrame(list(cycles)), "events": pd.DataFrame(list(events)),
            "ledger": pd.DataFrame(columns=["timestamp", "type", "amount_usd"]), "starting_capital": start,
            "insolvent": insolvent, "config": {"cost_labels": LABELS}}


class PaperAccountSummaryTest(SimpleTestCase):
    def test_drawdown_uses_intrabar_worst_and_a_peak_never_below_the_deposit(self):
        # equity 1000 -> 1200 -> 900 at the close, 600 at the bar's worst price
        out = pe.paper_account_summary("X/Y", _res([1000, 1200, 900], worst=[1000, 1200, 600]), 30)
        self.assertEqual(out["stats"]["max_dd"], -600.0)
        self.assertEqual(out["stats"]["max_dd_pct"], -50.0)                # 600 of the 1,200 peak
        out = pe.paper_account_summary("X/Y", _res([1000, 800, 900]), 30)
        self.assertEqual(out["stats"]["max_dd_pct"], -20.0)                # peak is the 1,000 deposit, not 800

    def test_status_precedence(self):
        liq = {"timestamp": T0, "type": sr.LIQUIDATED}
        bust = {"timestamp": T0 + pd.Timedelta(hours=1), "type": sr.BANKRUPTCY_OR_INSOLVENCY}
        self.assertEqual(pe.paper_account_summary("X/Y", _res([1000, 1100]), 30)["account"]["status"], "SURVIVED")
        self.assertEqual(pe.paper_account_summary("X/Y", _res([1000, 400]), 30)["account"]["status"], "DEPLETED")
        a = pe.paper_account_summary("X/Y", _res([1000, 1500], events=[liq]), 30)["account"]
        self.assertEqual((a["status"], a["liquidations"]), ("LIQUIDATED", 1))   # even though it ended up
        a = pe.paper_account_summary("X/Y", _res([1000, 50], events=[liq, bust], insolvent=True), 30)["account"]
        self.assertEqual(a["status"], "BUST")
        self.assertEqual(a["bust_at"], str(T0 + pd.Timedelta(hours=1))[:16])

    def test_pnl_by_fills_and_open_cycle_is_not_a_trade(self):
        cycles = [_cycle(0, 1, 10.0), _cycle(1, 1, -2.0), _cycle(2, 5, -300.0, sr.LIQUIDATED),
                  {**_cycle(3, 2, 4.0, sr.OPEN_UNRESOLVED_AT_DATA_END), "exit_ts": None, "exit_exec_price": None}]
        out = pe.paper_account_summary("X/Y", _res([1000, 708], cycles=cycles), 30)
        self.assertEqual(out["stats"]["total_trades"], 3)
        self.assertEqual(out["fill_dist"], {1: 2, 5: 1})
        by = {b["fills"]: b for b in out["pnl_by_fills"]}
        self.assertEqual((by[1]["cycles"], by[1]["wins"], by[1]["net"], by[1]["win_pct"]), (2, 1, 8.0, 50.0))
        self.assertEqual((by[5]["net"], by[5]["net_abs"], by[5]["liquidated"]), (-300.0, 300.0, 1))
        self.assertEqual(out["open_position"]["fills"], 2)
        self.assertEqual(out["stats"]["total_pnl"], -292.0)               # ending balance − deposit

    def test_curves_are_account_dollars(self):
        out = pe.paper_account_summary("X/Y", _res([1000, 1100, 1050]), 30)
        self.assertEqual(out["equity"][-1]["p"], 1050.0)                    # same day: last close
        self.assertEqual(out["drawdown"][-1]["p"], 0.0)                     # one daily point, at its own peak


class PaperAccountReplayTest(SimpleTestCase):
    """Real data, no mocking: GBP/JPY on the $3,100 account."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.r = pe.run_paper_account_simulation(["GBP/JPY"])["GBP/JPY"]

    def test_account_size_and_leverage(self):
        self.assertNotIn("error", self.r)
        self.assertEqual(pe.PAPER_ACCOUNT_USD, 3100.0)                      # $100 × (1 + 2 + 4 + 8 + 16)
        self.assertEqual((self.r["leverage"], self.r["entry_notional"]), (30, 3000))
        self.assertEqual(sr.esma_leverage("NZD/CHF"), 20.0)                 # not a major: 1:20

    def test_the_2022_close_out_is_shown(self):
        a = self.r["account"]
        self.assertGreaterEqual(a["liquidations"], 1)
        self.assertEqual(a["status"], "LIQUIDATED")
        self.assertTrue(a["first_liquidation"].startswith("2022-09-26"))

    def test_data_stop_at_the_fx_seal(self):
        from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
        self.assertLess(pd.Timestamp(self.r["window"]["end"], tz="UTC"), pd.Timestamp(FX_RESEARCH_DATA_CUTOFF))

    def test_drawdown_never_beyond_the_whole_account(self):
        self.assertGreaterEqual(self.r["stats"]["max_dd_pct"], -100.0)
        self.assertGreaterEqual(self.r["account"]["min_equity"], 0.0)
