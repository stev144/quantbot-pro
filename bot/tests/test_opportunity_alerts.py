# bot/tests/test_opportunity_alerts.py  # claude code changed: 2026-10-28 — new: phone alerts on state changes
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
from django.test import SimpleTestCase, override_settings

from bot.notifications import ntfy
from bot.research import opportunity_alerts as A

NOW = pd.Timestamp("2026-10-28 12:00", tz="UTC")


def _watch(statuses):
    coins = [{"symbol": s, "status": st, "f7_pct": 0.6 if st == "ON" else 0.1, "times_hurdle": 1.2 if st == "ON" else 0.2,
              "annual_equiv_pct": 31.0} for s, st in statuses.items()]
    return {"available": True, "coins": coins}


class Recorder:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, title, message, **kw):
        self.calls.append((title, kw.get("priority")))
        return self.ok


class RunTest(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, watch, send):
        return A.run(A.funding_conditions(watch), send, state_path=self.state, now=NOW)

    def test_first_run_is_silent_unless_actionable(self):
        rec = Recorder()
        self.run_with(_watch({"BTC/USDT": "BELOW_HURDLE", "ETH/USDT": "ON"}), rec)
        self.assertEqual([t for t, _ in rec.calls], ["ETH/USDT funding cleared the cost hurdle"])

    def test_alerts_only_on_change(self):
        rec = Recorder()
        w = _watch({"BTC/USDT": "BELOW_HURDLE"})
        self.run_with(w, rec)
        self.run_with(w, rec)
        self.assertEqual(rec.calls, [])                                        # nothing changed, nothing sent
        self.run_with(_watch({"BTC/USDT": "ON"}), rec)
        self.run_with(_watch({"BTC/USDT": "ON"}), rec)
        self.run_with(_watch({"BTC/USDT": "BELOW_HURDLE"}), rec)
        self.assertEqual([t for t, _ in rec.calls], ["BTC/USDT funding cleared the cost hurdle",
                                                     "BTC/USDT funding back below the hurdle"])

    def test_several_on_is_urgent(self):
        rec = Recorder()
        self.run_with(_watch({"A/USDT": "ON", "B/USDT": "ON", "C/USDT": "ON", "D/USDT": "BELOW_HURDLE"}), rec)
        self.assertIn(("3 coins clear the funding hurdle", "urgent"), rec.calls)

    def test_failed_push_is_retried_next_run(self):
        self.run_with(_watch({"BTC/USDT": "ON"}), Recorder(ok=False))
        rec = Recorder()
        self.run_with(_watch({"BTC/USDT": "ON"}), rec)
        self.assertEqual(len(rec.calls), 1)

    def test_dry_run_changes_nothing(self):
        sent = A.run(A.funding_conditions(_watch({"BTC/USDT": "ON"})), Recorder(), state_path=self.state, now=NOW,
                     dry_run=True)
        self.assertEqual(len(sent), 1)
        self.assertFalse(self.state.exists())


class FxcConditionsTest(SimpleTestCase):
    def _ledger(self, at, swap=19.0):
        return {"_ledger_events": [{"type": "SNAPSHOT", "at": at.isoformat(),
                                    "terms": {"USD/JPY": {"swap_long": swap}, "USD/CHF": {"swap_long": 3.0}}}]}

    def test_near_limit_uses_current_drawdown_and_stop(self):
        rep = {"days": 30, "stopped": None, "max_drawdown_pct": -8.0, "current_drawdown_pct": -2.0, "equity": 9800.0}
        st = {c.key: c.state for c in A.fxc_conditions(self._ledger(NOW), rep, NOW)}
        self.assertEqual(st["fxc:drawdown"], "OK")                             # recovered: worst-ever is not current
        rep["current_drawdown_pct"] = -7.5
        self.assertEqual({c.key: c.state for c in A.fxc_conditions(self._ledger(NOW), rep, NOW)}["fxc:drawdown"], "NEAR_LIMIT")
        rep["stopped"] = "2027-01-05"
        self.assertEqual({c.key: c.state for c in A.fxc_conditions(self._ledger(NOW), rep, NOW)}["fxc:drawdown"], "STOPPED")

    def test_stale_updates_and_carry_flip(self):
        st = {c.key: c.state for c in A.fxc_conditions(self._ledger(NOW - pd.Timedelta(days=4), swap=-2.0), None, NOW)}
        self.assertEqual(st["fxc:updates"], "STALE")
        self.assertEqual(st["fxc:carry:USD/JPY"], "NO_LONGER_PAYS")
        self.assertEqual(st["fxc:carry:USD/CHF"], "PAYS")


class NtfyTest(SimpleTestCase):
    @override_settings(NTFY_TOPIC="sqt-test", NTFY_SERVER="https://ntfy.example")
    def test_post_shape(self):
        seen = {}

        def post(url, data, headers, timeout):
            seen.update(url=url, data=data, headers=headers)
            return NS(status_code=200)
        self.assertTrue(ntfy.send("Hello", "body", priority="high", tags="x", click="http://a", post=post))
        self.assertEqual(seen["url"], "https://ntfy.example/sqt-test")
        self.assertEqual(seen["headers"]["Priority"], "4")
        self.assertEqual(seen["data"], b"body")

    @override_settings(NTFY_TOPIC="")
    def test_no_topic_sends_nothing(self):
        self.assertFalse(ntfy.send("t", "m", post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted"))))

    @override_settings(NTFY_TOPIC="sqt-test")
    def test_network_error_never_raises(self):
        def post(*a, **k):
            raise ConnectionError("down")
        self.assertFalse(ntfy.send("t", "m", post=post))
