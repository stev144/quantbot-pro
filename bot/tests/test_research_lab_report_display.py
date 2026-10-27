# claude code changed: 2026-10-26 — new file: Research Lab report display regressions (owner report, screenshots
# 2026-10-26 19:01): times were UTC without saying so, and a stored fraction (0.02645) was shown as "0.03%".
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from bot.templatetags.research_extras import as_pct


class AsPctFilterTest(SimpleTestCase):
    def test_known_answers(self):
        self.assertAlmostEqual(as_pct(0.02645033744631536), 2.645033744631536)
        self.assertAlmostEqual(as_pct(0.00021007601026887534), 0.021007601026887534)
        self.assertEqual(as_pct(None), "")
        self.assertEqual(as_pct(""), "")
        self.assertEqual(as_pct(0), 0.0)


class ReportRenderTest(SimpleTestCase):
    def _render(self):
        t = datetime(2026, 10, 26, 14, 54, 59, tzinfo=timezone.utc)
        exp = NS(id=uuid.UUID("8b0112f8-8c52-4d53-8f48-273db4acda89"), hypothesis_text="xrp falls when rsi > 80",
                 research_plan={"spec": {"asset": "XRP/USDT", "timeframe": "1h", "hypothesis_type": "conditional",
                                         "conditions": [{"feature": "rsi", "operator": ">", "threshold": 80}],
                                         "target": {"horizon": 4}, "direction": "negative"},
                                "data_availability": {"checks": [], "all_available": True}},
                 statistical_results={"metric": "conditional_event_test", "n_condition_true": 1164, "n_condition_false": 42843,
                                      "pct_condition_true": 0.02645033744631536,
                                      "mean_return_when_true": 0.00021007601026887534,
                                      "mean_return_when_false": 0.0001923187786784006,
                                      "median_return_when_true": -0.0022235836835823863,
                                      "median_return_when_false": 0.0},
                 created_at=t, started_at=t, completed_at=t, verdict="REJECTED", status="COMPLETED")
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        return render_to_string("research_lab/report.html", {"experiment": exp}, request=request)

    def test_percentage_and_utc_label(self):
        import re
        raw = self._render()
        html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw))   # the visible text
        self.assertIn("2.65% of observations", html)
        self.assertNotIn("0.03%", html)
        self.assertIn("0.0210%", html)                      # mean forward return, as a percentage
        self.assertIn("26 Oct 2026, 14:54 UTC", html)
        self.assertIn('class="local-time"', raw)             # the viewer's local time is added in the browser
        self.assertIn("your time", raw)                      # the local-time script is included


class BacktestReportTest(SimpleTestCase):
    """claude code changed: 2026-10-26 — strategy backtests used to fall through to "IC: not evaluated"."""

    def test_backtest_record_is_shown(self):
        import re
        t = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
        exp = NS(id=uuid.uuid4(), hypothesis_text="the platform strategy is profitable on BTC",
                 research_plan={"spec": {"asset": "BTC/USDT", "timeframe": "1h", "hypothesis_type": "strategy_backtest"},
                                "data_availability": {"checks": [], "all_available": True}},
                 statistical_results={"metric": "strategy_backtest", "n_trades": 1152, "n_trades_with_net_r": 1152,
                                      "mean_net_r": -0.66, "std_net_r": 1.2, "t_stat": -18.7, "p_value_one_sided": 1.0,
                                      "bootstrap_ci95_mean_net_r": [-0.73, -0.59], "lag1_autocorrelation": 0.02,
                                      "win_rate": 28.4, "profit_factor": 0.41, "initial_balance": 5000.0,
                                      "final_balance": 1.34, "max_drawdown": 99.97, "max_drawdown_usd": 4998.66,
                                      "data_start": "2025-01-01", "data_end": "2026-10-03"},
                 created_at=t, started_at=t, completed_at=t, verdict="REJECTED", status="COMPLETED", warnings=[])
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", render_to_string("research_lab/report.html", {"experiment": exp}, request=request)))
        self.assertIn("Strategy backtest", html)
        self.assertIn("-0.6600 / 1.2000", html)
        self.assertIn("-0.7300 to -0.5900", html)
        self.assertIn("−$4,998.66 (−99.97%)", html)
        self.assertNotIn("Information Coefficient", html)


class PValueFilterTest(SimpleTestCase):
    def test_known_answers(self):   # claude code changed: 2026-10-26
        from bot.templatetags.research_extras import pvalue
        self.assertEqual(pvalue(2.6711958e-05), "< 0.0001")
        self.assertEqual(pvalue(0.985), "0.9850")
        self.assertEqual(pvalue(0.0001), "0.0001")
        self.assertEqual(pvalue(None), "not evaluated")
