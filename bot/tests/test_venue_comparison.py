# claude code changed: new file — Venue Comparison page. Real network
# calls (compare_venues()/assess_venue_readiness() both hit real order
# books), no mocking, matching this project's convention.

from django.test import SimpleTestCase
from django.urls import reverse

from bot.views.venue_comparison_data import get_venue_comparison_table


class GetVenueComparisonTableTest(SimpleTestCase):

    def test_returns_all_three_venues_for_a_listed_symbol(self):   # claude code changed: 2026-10-22 — Coinbase added
        result = get_venue_comparison_table(symbol="BTC/USDT", quantity=0.01)

        self.assertTrue(result["available"])
        self.assertEqual(len(result["rows"]), 3)
        venue_ids = {row["venue_id"] for row in result["rows"]}
        self.assertEqual(venue_ids, {"binance", "kraken", "coinbase"})
        self.assertIsNotNone(result["cheapest_venue"])
        ranked = [r for r in result["rows"] if r["ranked"]]
        totals = [r["all_in_total"] for r in ranked]
        self.assertEqual(totals, sorted(totals))                    # claude code changed: 2026-10-22 — ranked by all-in amount paid
        self.assertEqual(result["cheapest_venue"], ranked[0]["venue_id"])
        for r in ranked:                                            # all-in = qty × fill × (1 + fee), recomputed independently
            self.assertAlmostEqual(r["all_in_total"], 0.01 * r["estimated_fill_price"] * (1 + r["fee_rate_pct"] / 100), places=9)
        self.assertEqual(ranked[0]["extra_vs_cheapest"], 0.0)
        for r in result["rows"]:
            self.assertEqual(r["market"], "BTC/USDT")                # listed directly on all three
            self.assertFalse(r["quote_mismatch"])
        kraken = next(r for r in result["rows"] if r["venue_id"] == "kraken")
        self.assertTrue(kraken["fee_verified"])
        self.assertEqual(kraken["fee_rate_pct"], 0.8)

    def test_a_usd_fallback_is_labelled_not_hidden(self):   # claude code changed: 2026-10-22 — Kraken audit finding
        result = get_venue_comparison_table(symbol="AAVE/USDT", quantity=1.0)
        by = {r["venue_id"]: r for r in result["rows"]}
        self.assertEqual(by["kraken"]["market"], "AAVE/USD")
        self.assertTrue(by["kraken"]["quote_mismatch"])
        self.assertEqual(by["coinbase"]["market"], "AAVE/USD")
        self.assertTrue(result["any_quote_mismatch"])
        self.assertFalse(by["binance"]["quote_mismatch"])
        self.assertFalse(by["kraken"]["ranked"])                    # claude code changed: 2026-10-22 — a USD row is never ranked against USDT
        self.assertIsNone(by["kraken"]["rank"])

    def test_an_inactive_market_is_reported_as_such(self):   # claude code changed: 2026-10-22 — MATIC delisted on Binance
        result = get_venue_comparison_table(symbol="MATIC/USDT", quantity=1.0)
        binance = next(r for r in result["rows"] if r["venue_id"] == "binance")
        self.assertFalse(binance["available"])
        self.assertIn("inactive", binance["unavailable_reason"])

    def test_flags_a_symbol_kraken_does_not_list(self):
        # claude code changed: MATIC/USDT — Step 5's confirmed real,
        # fully-unlisted case on Kraken (Polygon rebranded to POL).
        result = get_venue_comparison_table(symbol="MATIC/USDT", quantity=1.0)

        self.assertTrue(result["available"])
        kraken_row = next(r for r in result["rows"] if r["venue_id"] == "kraken")
        self.assertFalse(kraken_row["available"])
        self.assertIn("not tradeable", kraken_row["unavailable_reason"])


class VenueComparisonViewTest(SimpleTestCase):

    def test_page_renders_with_default_symbol(self):
        response = self.client.get(reverse("venue_comparison"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Venue Comparison", html)
        self.assertIn("BINANCE", html)
        self.assertIn("KRAKEN", html)
        self.assertIn("COINBASE", html)   # claude code changed: 2026-10-22
        self.assertIn("unverified", html)  # claude code changed: fee provenance is shown

    def test_page_accepts_a_custom_symbol_and_quantity(self):
        response = self.client.get(reverse("venue_comparison"), {"symbol": "BTC/USDT", "quantity": "0.02"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CHEAPEST", response.content)

    def test_invalid_quantity_falls_back_to_default(self):
        response = self.client.get(reverse("venue_comparison"), {"quantity": "not-a-number"})

        self.assertEqual(response.status_code, 200)


from unittest import mock   # noqa: E402  # claude code changed: 2026-10-22 — ranking known answer

from bot.engines.execution_comparison import VenueQuote   # noqa: E402
from bot.views import venue_comparison_data as vcd   # noqa: E402


def _q(venue, sym, fill, fee, slip):
    return VenueQuote(venue_id=venue, symbol=sym, available=True, unavailable_reason="", best_bid=fill - 0.01,
                      best_ask=fill, spread_pct=0.0001, fee_rate=fee, estimated_fill_price=fill,
                      estimated_slippage_pct=slip, estimated_total_cost_pct=fee + slip)


class AllInRankingTest(SimpleTestCase):   # claude code changed: 2026-10-22 — the CHEAPEST badge follows the amount actually paid
    def test_lower_percentage_but_higher_price_does_not_win(self):
        # A: fee 0.10 %, book 1 % higher (fill 101.0)  → all-in 101.101 × 10 = 1011.01
        # B: fee 0.20 %, fill 100.0                    → all-in 100.200 × 10 = 1002.00   ← cheaper in money
        # The old metric (fee + own-mid slippage) would have ranked A first (0.10 % < 0.20 %).
        quotes = [_q("binance", "X/USDT", 101.0, 0.001, 0.0), _q("kraken", "X/USDT", 100.0, 0.002, 0.0),
                  _q("coinbase", "X/USD", 99.0, 0.006, 0.0)]
        with mock.patch.object(vcd, "compare_venues", return_value=quotes), \
             mock.patch.object(vcd, "assess_venue_readiness", side_effect=Exception("offline")):
            r = vcd.get_venue_comparison_table("X/USDT", 10.0)
        self.assertEqual(r["cheapest_venue"], "kraken")
        by = {row["venue_id"]: row for row in r["rows"]}
        self.assertAlmostEqual(by["kraken"]["all_in_total"], 1002.0, places=9)
        self.assertAlmostEqual(by["binance"]["all_in_total"], 1011.01, places=9)
        self.assertAlmostEqual(by["binance"]["extra_vs_cheapest"], 9.01, places=9)
        self.assertEqual((by["kraken"]["rank"], by["binance"]["rank"]), (1, 2))
        self.assertFalse(by["coinbase"]["ranked"])              # USD row shown, never ranked against USDT
        self.assertEqual([row["venue_id"] for row in r["rows"]], ["kraken", "binance", "coinbase"])
        self.assertEqual(by["binance"]["readiness"], "UNKNOWN")   # a failed readiness check is reported, not invented


class CardAnimationTest(SimpleTestCase):   # claude code changed: 2026-10-22 — card values count up from 0 (counters.js)
    def test_plain_never_uses_exponent_notation(self):
        self.assertEqual(vcd._plain(6.93e-06), "0.00000693")
        self.assertEqual(vcd._plain(104.68), "104.68")
        self.assertEqual(vcd._plain(10484.474, 4, 2), "10484.474")
        self.assertEqual(vcd._plain(0.0, 4, 2), "0.00")
        self.assertIsNone(vcd._plain(None))

    def test_card_values_are_animated_leaf_elements_and_labels_are_not(self):
        import re
        quotes = [_q("binance", "X/USDT", 0.00000693, 0.001, 0.0), _q("kraken", "X/USDT", 0.000007, 0.008, 0.0)]
        with mock.patch.object(vcd, "compare_venues", return_value=quotes), \
             mock.patch.object(vcd, "assess_venue_readiness", side_effect=Exception("offline")):
            html = self.client.get("/execution/venues/?symbol=X/USDT&quantity=1000000").content.decode()
        cells = re.findall(r'<(\w+) class="[^"]*count-text[^"]*">([^<]*)</\1>', html)
        self.assertGreaterEqual(len(cells), 15)                      # 7 per ranked card + the runner-up's gap line
        self.assertTrue(all("e-" not in text for _, text in cells))  # no exponent notation to scramble
        self.assertIn("counters.js", html)
        self.assertNotIn('class="vc-ribbon vc-r2 count-text"', html)   # rank pills ("2ND") never animate


# claude code changed: 2026-10-26 — display fixes: one price precision per comparison, and no multi-line {# #} comments
class PriceDisplayPrecisionTest(SimpleTestCase):
    def test_shared_precision_keeps_trailing_zeros(self):
        from bot.views.venue_comparison_data import _fixed, _price_decimals
        rows = [{"best_bid": 1.2909, "best_ask": 1.291}, {"best_bid": 1.29073, "best_ask": 1.29113},
                {"best_bid": None, "best_ask": None}]
        d = _price_decimals(rows)
        self.assertEqual(d, 5)                                   # Kraken's 5-decimal tick sets the precision
        self.assertEqual(_fixed(1.291, d), "1.29100")            # was "1.291" next to "1.2909"
        self.assertEqual(_fixed(1.29161442, d), "1.29161")       # was 8 decimals
        self.assertIsNone(_fixed(None, d))

    def test_precision_bounds(self):
        from bot.views.venue_comparison_data import _price_decimals
        self.assertEqual(_price_decimals([{"best_bid": 64000.0, "best_ask": 64000.5}]), 2)   # never fewer than 2
        self.assertEqual(_price_decimals([{"best_bid": 0.0000069312345, "best_ask": 0.000007}]), 8)   # capped at 8
        self.assertEqual(_price_decimals([]), 2)


class NoMultilineTemplateCommentTest(SimpleTestCase):
    def test_every_hash_comment_closes_on_its_own_line(self):
        """Django's {# #} is single-line only; an unclosed one is printed on the page (it leaked on Venue Comparison)."""
        from pathlib import Path
        from django.conf import settings
        bad = []
        for path in Path(settings.BASE_DIR, "templates").rglob("*.html"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.count("{#") > line.count("#}"):
                    bad.append(f"{path.relative_to(settings.BASE_DIR)}:{n}")
        self.assertEqual(bad, [], "use {% comment %}…{% endcomment %} for multi-line comments")
