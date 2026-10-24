# claude code changed: new file — 2026-10-22, coin logos ({% coin_logo %}) on the crypto pages.
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from bot.templatetags import coin_extras as ce

LOGOS = Path(settings.BASE_DIR) / "static" / "img" / "coins"
render = lambda src, **ctx: Template("{% load coin_extras %}" + src).render(Context(ctx))


class CoinLogoTagTests(SimpleTestCase):
    def test_every_symbol_format_resolves_to_the_same_logo(self):
        for s in ("BTC_USDT", "BTC/USDT", "BTCUSDT", "btc"):
            html = render("{% coin_logo s %}", s=s)
            self.assertIn('src="/static/img/coins/btc.svg"', html, s)
            self.assertIn('alt="BTC logo"', html)

    def test_a_coin_without_a_logo_gets_a_lettered_badge_not_a_broken_image(self):
        html = render("{% coin_logo 'ACM_USDT' 30 %}")
        self.assertIn("coin-logo-fallback", html)
        self.assertIn(">AC<", html)
        self.assertNotIn("<img", html)

    def test_size_is_clamped_and_bad_input_renders_nothing(self):
        self.assertIn('width="96"', render("{% coin_logo 'BTC' 500 %}"))
        self.assertIn('width="24"', render("{% coin_logo 'BTC' 'x' %}"))
        self.assertEqual(render("{% coin_logo s %}", s='<script>"'), "")

    def test_bundled_logos_are_local_and_inert(self):
        files = list(LOGOS.glob("*.svg"))
        self.assertGreaterEqual(len(files), 150)
        bad = re.compile(r"<script|\son\w+\s*=|javascript:|https?://(?!www\.w3\.org)", re.I)
        self.assertEqual([f.name for f in files if bad.search(f.read_text(encoding="utf-8"))], [])
        for lic in ("LICENSE-web3icons.txt", "LICENSE-cryptocurrency-icons.md", "README.txt"):
            self.assertTrue((LOGOS / lic).exists(), lic)


class CoinLogoPagesTests(TestCase):
    QUOTE = {"available": True, "symbol": "BTC_USDT", "display_symbol": "BTC/USDT", "display_name": "Bitcoin",
             "asset_class": "crypto", "price": 65000.0, "change_pct": 1.0, "positive": True, "sparkline": "",
             "price_decimals": 2, "as_of": "2026-10-22T00:00:00+00:00", "tail": []}
    FX = {**QUOTE, "symbol": "EUR_USD", "display_symbol": "EUR/USD", "display_name": "", "asset_class": "forex"}

    def test_markets_cards_show_logos_for_crypto_only(self):
        from bot.views import markets_data as md
        with mock.patch.object(md, "get_markets_list", return_value={"crypto": [dict(self.QUOTE)], "forex": [dict(self.FX)],
                                                                    "n_crypto": 1, "n_forex": 1}):
            html = self.client.get("/markets/").content.decode()
        self.assertEqual(html.count('src="/static/img/coins/btc.svg"'), 1)
        self.assertNotIn("img/coins/eur", html)

    def test_venue_comparison_cards_show_the_compared_coin(self):
        from bot.engines.execution_comparison import VenueQuote
        from bot.views import venue_comparison_data as vcd
        q = VenueQuote("binance", "BTC/USDT", True, "", 1.0, 1.1, 0.0001, 0.001, 1.1, 0.0, 0.001)
        with mock.patch.object(vcd, "compare_venues", return_value=[q]), \
             mock.patch.object(vcd, "assess_venue_readiness", side_effect=Exception("offline")):
            html = self.client.get("/execution/venues/?symbol=BTC/USDT&quantity=1").content.decode()
        self.assertIn('src="/static/img/coins/btc.svg"', html)


FLAGS = Path(settings.BASE_DIR) / "static" / "img" / "flags"


class FxPairTagTests(SimpleTestCase):   # claude code changed: 2026-10-22 — forex flags + full names
    def test_pair_container_shows_both_flags_and_full_names(self):
        html = render("{% fx_pair s %}", s="EUR_USD")
        self.assertIn('src="/static/img/flags/eu.svg"', html)
        self.assertIn('src="/static/img/flags/us.svg"', html)
        self.assertIn("Euro", html)
        self.assertIn("US Dollar", html)
        self.assertLess(html.index("eu.svg"), html.index("us.svg"))          # base flag first (in front)
        for s in ("EUR/USD", "EURUSD", "eur_usd"):
            self.assertEqual(render("{% fx_pair s %}", s=s), html, s)

    def test_metals_offshore_yuan_and_unknown_codes(self):
        self.assertIn("xau.svg", render("{% fx_pair 'XAU_USD' %}"))
        self.assertIn("Gold", render("{% fx_pair 'XAU_USD' %}"))
        self.assertIn("Silver", render("{% fx_pair 'XAG_USD' %}"))
        self.assertIn("cn.svg", render("{% fx_pair 'USD_CNH' %}"))
        unknown = render("{% fx_pair 'USD_ABC' %}")
        self.assertIn("coin-logo-fallback", unknown)                          # code badge, never a guessed flag
        self.assertIn(">ABC<", unknown)
        self.assertEqual(render("{% fx_pair 'BTC' %}"), "")                    # not a 3+3 pair

    def test_every_currency_in_the_forex_universe_has_a_flag_and_name(self):
        import glob, os
        codes = set()
        for f in glob.glob(os.path.join(settings.BASE_DIR, "data", "forex", "*_1h.csv")):
            name = os.path.basename(f)[:-len("_1h.csv")]
            if "5m" in name or "15m" in name:
                continue
            codes.update(name.split("_"))
        self.assertTrue(codes)
        self.assertEqual(sorted(c for c in codes if c not in ce.CURRENCIES), [])
        self.assertEqual(sorted(c for c in codes if not (FLAGS / f"{ce.CURRENCIES[c][0]}.svg").exists()), [])

    def test_bundled_flags_are_local_and_inert(self):
        bad = re.compile(r"<script|\son\w+\s*=|javascript:|href\s*=\s*[\"']https?:", re.I)
        files = list(FLAGS.glob("*.svg"))
        self.assertEqual(len(files), 21)
        self.assertEqual([f.name for f in files if bad.search(f.read_text(encoding="utf-8"))], [])
        self.assertTrue((FLAGS / "LICENSE-flag-icons.txt").exists())


class FxPairPagesTests(TestCase):
    def test_markets_forex_card_has_the_flag_container(self):
        from bot.views import markets_data as md
        fx = {**CoinLogoPagesTests.FX, "symbol": "GBP_JPY", "display_symbol": "GBP/JPY"}
        with mock.patch.object(md, "get_markets_list", return_value={"crypto": [], "forex": [fx], "n_crypto": 0, "n_forex": 1}):
            html = self.client.get("/markets/").content.decode()
        self.assertIn('class="fx-pair"', html)
        self.assertIn("British Pound", html)
        self.assertIn("Japanese Yen", html)
