# claude code changed: new file — Kraken Multi-Venue Execution, Step 18.
# Renders the real execution_monitor view via Django's test client with a
# temporary TradeRecord, confirming the new Venue column actually surfaces
# real data (not just that the template is syntactically valid). No fake
# data is left in the dev DB — TestCase wraps this in a transaction that
# rolls back automatically.

from django.test import TestCase
from django.urls import reverse

from bot.journal.models import TradeRecord


class ExecutionMonitorVenueColumnTest(TestCase):

    def test_open_position_shows_venue_badge(self):
        TradeRecord.objects.create(
            symbol="BTC/USDT", side="BUY", venue="kraken",
            entry_price=50000.0, sl=49000.0, tp=53000.0, quantity=0.1,
            order_id="test-open", status="OPEN",
        )

        response = self.client.get(reverse("execution_monitor"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Venue", html)
        self.assertIn('<span class="badge dim">KRAKEN</span>', html)

    def test_closed_trade_shows_venue_badge(self):
        TradeRecord.objects.create(
            symbol="ETH/USDT", side="BUY", venue="binance",
            entry_price=3000.0, sl=2940.0, tp=3120.0, quantity=1.0,
            order_id="test-closed", status="WIN",
            exit_price=3100.0, net_pnl=100.0, r_multiple=1.5,
        )

        response = self.client.get(reverse("execution_monitor"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<span class="badge dim">BINANCE</span>', html)

    def test_liquidity_reason_no_longer_claims_no_metric_exists(self):
        # claude code changed: regression guard on the terminal_data.py
        # reason-string fix — the old, now-false claim must never reappear.
        from bot.views.terminal_data import get_market_state
        from bot.engines.regime_detector import RegimeResult

        regime_result = RegimeResult(
            regime="RANGING", confidence="HIGH", adx=15.0, atr_ratio=1.0,
            ema_spread_pct=0.1, bb_width_pct=1.5, adx_trending=False,
            volatility_extreme=False, summary="test",
        )
        state = get_market_state(regime_result, {"available": False})

        self.assertFalse(state["liquidity"]["available"])
        self.assertNotIn("No volume/order-book-based liquidity metric exists", state["liquidity"]["reason"])
        self.assertIn("bot/engines/liquidity.py", state["liquidity"]["reason"])
