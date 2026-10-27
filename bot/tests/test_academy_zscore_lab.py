# claude code changed: 2026-10-26 — new file: Z-Score Exploration Lab (bot/academy/zlab.py + page). Known answers for
# every claim the page makes; the page's JavaScript uses the same formula (checked in a real browser during review).
import math

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bot.academy import zlab


class ZLabMathsTest(SimpleTestCase):
    def test_default_example_is_exact(self):
        r = zlab.compute(zlab.DEFAULT_SAMPLE, zlab.DEFAULT_X)
        self.assertEqual(r["n"], 8)
        self.assertAlmostEqual(r["mean"], 100.0)
        self.assertAlmostEqual(r["sd"], 2.0)          # squared deviations 28, divided by n − 1 = 7 → 4
        self.assertAlmostEqual(r["z"], 2.0)
        self.assertIn("unusual for this sample", r["interpretation"])

    def test_volatile_sample_shrinks_z(self):
        r = zlab.compute([90, 110, 95, 105, 100, 85, 115, 100], 104)
        self.assertAlmostEqual(r["sd"], 10.0)         # squared deviations 700 / 7 = 100
        self.assertAlmostEqual(r["z"], 0.4)

    def test_outlier_raises_sd_and_shrinks_everyone(self):
        base = zlab.compute(zlab.DEFAULT_SAMPLE, 104)
        withx = zlab.compute(zlab.DEFAULT_SAMPLE + [130.0], 104)
        self.assertGreater(withx["sd"], base["sd"])
        self.assertLess(abs(withx["z"]), abs(base["z"]))

    def test_edge_cases(self):
        self.assertIsNone(zlab.compute([5.0], 6)["z"])
        self.assertIn("at least two", zlab.compute([], 1)["reason"])
        flat = zlab.compute([3.0, 3.0, 3.0], 4)
        self.assertIsNone(flat["z"])
        self.assertEqual(flat["sd"], 0.0)
        self.assertAlmostEqual(zlab.compute([1, 2, 3], 1)["z"], -1.0)
        self.assertIn("below", zlab.compute([1, 2, 3], 1)["interpretation"])
        self.assertIn("exactly at", zlab.compute([1, 2, 3], 2)["interpretation"])

    def test_parsing(self):
        self.assertEqual(zlab.parse_sample("98, 101 100;103\n abc, nan, inf, 1e2"), [98, 101, 100, 103, 100])
        self.assertEqual(len(zlab.parse_sample(" ".join(["1"] * 80))), zlab.MAX_POINTS)
        self.assertEqual(zlab.parse_sample(""), [])

    def test_sample_sd_matches_statistics_module(self):
        import statistics
        s = [3.1, 4.7, 2.2, 9.9, 5.5]
        self.assertAlmostEqual(zlab.compute(s, 0)["sd"], statistics.stdev(s))
        self.assertAlmostEqual(zlab.compute(s, 0)["mean"], statistics.mean(s))


class ZLabPageTest(TestCase):
    def test_public_page_server_render(self):
        r = self.client.get(reverse("academy_zscore_lab"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ILLUSTRATIVE NUMBERS")
        self.assertContains(r, 'data-server-z="2.000000"')
        self.assertContains(r, "An extreme Z is not a forecast")

    def test_query_parameters_without_javascript(self):
        r = self.client.get(reverse("academy_zscore_lab"), {"sample": "1 2 3", "x": "1"})
        self.assertContains(r, 'data-server-z="-1.000000"')
        r = self.client.get(reverse("academy_zscore_lab"), {"sample": "4 4 4", "x": "5"})
        self.assertContains(r, "standard deviation is 0")
        r = self.client.get(reverse("academy_zscore_lab"), {"sample": "1 2 3", "x": "not-a-number"})
        self.assertEqual(r.status_code, 200)
