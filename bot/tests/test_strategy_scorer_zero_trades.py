# claude code changed: new — regression test for a real bug found while
# investigating a dashboard report of "hardcoded-looking" Scorer Notes
# (Loss streak of 999, HIGH RISK, 0 trades all shown together). Two
# separate sentinel-default bugs in StrategyScorer.__init__() compounded:
#   1. _resolve_loss_streak() used `results.get(..., 999) or 999` — the
#      `or` treats a legitimate 0 the same as "key missing," substituting
#      the 999 sentinel for a 0-trade backtest's true (and trivial) 0
#      consecutive losses.
#   2. max_drawdown defaulted to 100.0 (worst case) unconditionally,
#      instead of 0.0 for a 0-trade backtest whose equity curve never
#      moved at all.
# Together these forced _resolve_strategy_health() into "HIGH RISK" for
# ANY symbol/period that simply never traded — presenting "no evidence
# either way" as "confirmed bad strategy," which is what made the notes
# look identical/templated across different, genuinely-empty backtests.

from django.test import SimpleTestCase

from bot.engines.strategy_scorer import StrategyScorer


class ZeroTradeBacktestTest(SimpleTestCase):

    def test_zero_trades_reports_honest_zero_not_a_999_sentinel(self):
        scorer = StrategyScorer({"trades": [], "sharpe_ratio": 0.0})
        result = scorer.evaluate()

        self.assertEqual(result["metrics"]["total_trades"], 0)
        self.assertEqual(result["metrics"]["loss_streak"], 0)
        self.assertEqual(result["metrics"]["max_drawdown"], 0.0)

    def test_zero_trades_no_longer_forces_high_risk(self):
        # claude code changed: the false 999/100.0 sentinels alone were
        # enough to trip the HIGH RISK branch regardless of anything else —
        # confirms that specific false trigger is gone. Not asserting a
        # specific replacement health label (a genuinely empty backtest
        # legitimately still scores low on sample size/profitability), only
        # that it's no longer the misleading "confirmed bad strategy" claim.
        scorer = StrategyScorer({"trades": [], "sharpe_ratio": 0.0})
        result = scorer.evaluate()

        self.assertNotEqual(result["strategy_health"], "HIGH RISK")
        self.assertNotIn("HIGH RISK", result["verdict"])

    def test_real_reported_streak_is_still_honored_when_trades_list_is_empty(self):
        # claude code changed: a caller that genuinely has 0 trades in the
        # list but a real, independently-reported max_consecutive_losses
        # value must still see that real value, not the sentinel — only an
        # ACTUALLY-missing key falls back now.
        scorer = StrategyScorer({"trades": [], "max_consecutive_losses": 3, "sharpe_ratio": 0.0})
        result = scorer.evaluate()
        self.assertEqual(result["metrics"]["loss_streak"], 3)

    def test_nonzero_trades_with_missing_drawdown_key_still_fails_safe(self):
        # claude code changed: the max_drawdown fix is conditional on an
        # EMPTY trades list specifically — a backtest that DID trade but
        # whose results dict is genuinely missing "max_drawdown" (a
        # different, real failure mode) must still fail safe to the
        # conservative 100.0, not silently default to 0.0.
        trades = [{"pnl": 10, "r_multiple": 1.0}, {"pnl": -5, "r_multiple": -0.5}]
        scorer = StrategyScorer({"trades": trades, "sharpe_ratio": 0.0})
        self.assertEqual(scorer.max_drawdown, 100.0)
