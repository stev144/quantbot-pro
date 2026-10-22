# claude code changed: 2026-10-22 — new: the Regime page (/market/) renders the SAME Markets cards as /markets/
# (shared partials), with the regime strip and the z-score strip; Markets cards carry neither strip.
from unittest import mock

from django.test import TestCase

from bot.views import market_intelligence_data as mid
from bot.views import markets_data as md

QUOTE = {"available": True, "symbol": "BTC_USDT", "display_symbol": "BTC/USDT", "display_name": "Bitcoin",
         "asset_class": "crypto", "price": 65000.0, "change_pct": 1.25, "positive": True, "sparkline": "",
         "price_decimals": 2, "as_of": "2026-10-22T00:00:00+00:00", "tail": []}
ROW = {"symbol": "BTC_USDT", "regime": "TRENDING_UP", "confidence": "HIGH", "adx": 31.0, "atr_ratio": 1.9,
       "ema_spread_pct": 0.6, "bb_width_pct": 2.5, "volatility_extreme": False, "summary": ""}
CONTEXT = {
    "regime_overview": {"available": True, "rows": [ROW, {**ROW, "symbol": "NOFILE_USDT"}], "n_symbols": 2,
                        "n_errors": 0, "errors": {}, "regime_counts": {"TRENDING_UP": 2}, "candles_per_symbol": 300,
                        "fetch_aborted": False, "aborted_reason": "", "reason": ""},
    "cross_section": {"available": True, "dispersion_std": 1.0, "n_symbols": 3, "as_of": "2026-09-24 22:00 UTC",
                      "n_excluded_other_timestamp": 2, "top_extremes": [{"symbol": "BTC_USDT", "cs_zscore": -2.5}]},
}


class RegimePageCardsTests(TestCase):
    def render(self):
        with mock.patch.object(mid, "get_market_intelligence", return_value={k: dict(v) for k, v in CONTEXT.items()}), \
             mock.patch.object(md, "get_markets_list", return_value={"crypto": [dict(QUOTE)], "forex": []}):
            r = self.client.get("/market/")
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_cards_not_table(self):
        html = self.render()
        self.assertNotIn('class="term-table"', html)
        self.assertEqual(html.count('class="mkt-card"'), 2)              # 1 regime card + 1 z-score card
        self.assertIn('data-regime="TRENDING_UP"', html)
        self.assertIn("ADX <b>31.0</b>", html)
        self.assertIn('class="warn"', html)                               # ATR ratio 1.9 > 1.7 flagged
        self.assertIn("1 classified symbol(s) have no local price file", html)

    def test_zscore_card_states_its_real_time(self):
        html = self.render()
        self.assertIn(">z -2.5<", html)
        self.assertIn("at <b>2026-09-24 22:00 UTC</b>", html)
        self.assertNotIn("right now", html)

    def test_markets_cards_carry_no_research_strip(self):
        with mock.patch.object(md, "get_markets_list", return_value={"crypto": [dict(QUOTE)], "forex": [], "n_crypto": 1, "n_forex": 0}):
            html = self.client.get("/markets/").content.decode()
        self.assertEqual(html.count('class="mkt-card"'), 1)
        self.assertNotIn('class="mkt-strip"', html)
        self.assertEqual(html.count("function toggleWatchlist"), 1)


class ForexDashboardCardsTests(TestCase):   # claude code changed: 2026-10-22 — the Forex dashboard's z-score list uses the same cards
    def test_zscore_list_renders_markets_cards(self):
        from bot.views import forex_dashboard as fd
        fx = {**QUOTE, "symbol": "GBP_JPY", "display_symbol": "GBP/JPY", "display_name": "", "asset_class": "forex",
              "price": 199.5, "price_decimals": 5}
        disp = {"available": True, "dispersion_std": 1.0, "n_symbols": 2, "as_of": "2026-09-18 22:00 UTC",
                "n_excluded_other_timestamp": 0,
                "top_extremes": [{"symbol": "GBP_JPY", "cs_zscore": 1.704}, {"symbol": "NOFILE_JPY", "cs_zscore": -1.0}]}
        with mock.patch.object(fd, "get_forex_cross_sectional_dispersion", return_value=disp), \
             mock.patch.object(md, "get_markets_list", return_value={"crypto": [], "forex": [fx]}):
            html = self.client.get("/forex/").content.decode()
        self.assertNotIn('<th class="num">Z-Score</th>', html)
        self.assertEqual(html.count('class="mkt-card"'), 1)
        self.assertIn("mkt-class-badge forex", html)
        self.assertIn(">z 1.704<", html)
        self.assertIn('class="badge neu"', html)                  # neutral colour: a z-score is position, not good/bad
        self.assertIn("1 instrument(s) have no local price file", html)
