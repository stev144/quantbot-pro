# claude code changed: new file — Venue Comparison page. Real network
# calls (compare_venues()/assess_venue_readiness() both hit real order
# books), no mocking, matching this project's convention.

from django.test import SimpleTestCase
from django.urls import reverse

from bot.views.venue_comparison_data import get_venue_comparison_table


class GetVenueComparisonTableTest(SimpleTestCase):

    def test_returns_both_venues_for_a_listed_symbol(self):
        result = get_venue_comparison_table(symbol="BTC/USDT", quantity=0.01)

        self.assertTrue(result["available"])
        self.assertEqual(len(result["rows"]), 2)
        venue_ids = {row["venue_id"] for row in result["rows"]}
        self.assertEqual(venue_ids, {"binance", "kraken"})
        self.assertIsNotNone(result["cheapest_venue"])

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

    def test_page_accepts_a_custom_symbol_and_quantity(self):
        response = self.client.get(reverse("venue_comparison"), {"symbol": "BTC/USDT", "quantity": "0.02"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CHEAPEST", response.content)

    def test_invalid_quantity_falls_back_to_default(self):
        response = self.client.get(reverse("venue_comparison"), {"quantity": "not-a-number"})

        self.assertEqual(response.status_code, 200)
