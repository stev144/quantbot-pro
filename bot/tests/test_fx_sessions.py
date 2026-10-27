# claude code changed: 2026-10-26 — new file: forex pair trading-session labels (coin_extras.fx_sessions / fx_session)
from django.test import SimpleTestCase

from bot.templatetags.coin_extras import fx_pair, fx_session, fx_sessions


class FxSessionTest(SimpleTestCase):
    def test_known_pairs(self):
        self.assertEqual(fx_sessions("EUR/USD"), ["London", "New York"])
        self.assertEqual(fx_sessions("USD_JPY"), ["Tokyo", "New York"])
        self.assertEqual(fx_sessions("AUDJPY"), ["Sydney", "Tokyo"])
        self.assertEqual(fx_sessions("EUR/GBP"), ["London"])
        self.assertEqual(fx_sessions("GBP/JPY"), ["Tokyo", "London"])
        self.assertEqual(fx_sessions("XAU/USD"), ["London", "New York"])
        self.assertEqual(fx_sessions("NZD/CAD"), ["Sydney", "New York"])

    def test_not_a_pair(self):
        self.assertEqual(fx_sessions("BTC_USDT"), [])
        self.assertEqual(fx_session("BTC_USDT"), "")

    def test_labels(self):
        self.assertIn("London · New York sessions", fx_session("EUR/USD"))
        self.assertIn("London session", fx_session("EUR/GBP"))
        self.assertIn("fx-session", fx_pair("EUR/USD"))      # every fx_pair display carries it
