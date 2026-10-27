# claude code changed: 2026-10-26 — new file. "From Correlation to Cointegration" (bot/academy/content/
# strategy_engineering/cointegration/*.md): every worked-example number is recomputed here AND checked against the
# lesson text, so a typo in a lesson fails the suite (Gate 3). Also checks the seed and that the quiz answers agree.
import math
from pathlib import Path

import numpy as np
from django.conf import settings
from django.test import SimpleTestCase

from bot.academy.curriculum_seed import AUTHORED_COURSES, CORRELATION_TO_COINTEGRATION_COURSE as COURSE

CONTENT = Path(settings.BASE_DIR) / "bot" / "academy" / "content"


def _lesson(n):
    files = [l["content_file"] for m in COURSE["modules"] for l in m["lessons"]]
    return (CONTENT / files[n - 1]).read_text(encoding="utf-8")


class SeedTest(SimpleTestCase):
    def test_course_is_registered_with_four_lessons_and_files_exist(self):
        self.assertIn(COURSE, AUTHORED_COURSES)
        lessons = [l for m in COURSE["modules"] for l in m["lessons"]]
        self.assertEqual(len(lessons), 4)
        for l in lessons:
            self.assertTrue((CONTENT / l["content_file"]).exists(), l["content_file"])

    def test_quiz_answers_are_well_formed(self):
        for q in COURSE["quiz"]["questions"]:
            if q["question_type"] == "multiple_choice":
                self.assertLess(q["payload"]["correct_index"], len(q["payload"]["choices"]))
            self.assertTrue(q["explanation"])


class Lesson1CorrelationTest(SimpleTestCase):
    def test_worked_example(self):
        a = np.array([1, -2, 3, 0, 2.0]); b = np.array([2, -1, 2, -1, 3.0])
        self.assertAlmostEqual(a.mean(), 0.8); self.assertAlmostEqual(b.mean(), 1.0)
        da, db = a - a.mean(), b - b.mean()
        self.assertAlmostEqual(float((da * db).sum()), 12.0)
        self.assertAlmostEqual(float((da ** 2).sum()), 14.8)
        self.assertAlmostEqual(float((db ** 2).sum()), 14.0)
        r = float(np.corrcoef(a, b)[0, 1])
        self.assertAlmostEqual(r, 12.0 / math.sqrt(14.8 * 14.0))
        text = _lesson(1)
        for s in ("**0.8%**", "**1.0%**", "**12.0**", "**14.8**", "**14.0**", "14.394", f"**{r:.3f}**"):
            self.assertIn(s, text)


class Lesson2SpreadTest(SimpleTestCase):
    def test_worked_example(self):
        a = np.array([102.0, 104.0, 101.0, 106.0]); b = np.array([50.0, 51.5, 50.0, 52.5])
        self.assertEqual(list(a - 2 * b), [2.0, 1.0, 1.0, 1.0])
        self.assertEqual(110 - 2 * 54, 2)                       # the quick check
        text = _lesson(2)
        self.assertIn("102 − 100 = **2**", text)
        self.assertIn("106 − 105 = **1**", text)
        self.assertIn("110 − 2 × 54 = 2", text)


class Lesson3StationarityTest(SimpleTestCase):
    def test_worked_examples(self):
        self.assertAlmostEqual(float(np.mean([20.5, 19.8, 20.2, 19.6, 20.4])), 20.1)
        # ±1 random walk: root-mean-square distance after n steps is sqrt(n) (simulated check, then the formula)
        rng = np.random.default_rng(1)
        steps = rng.choice([-1, 1], size=(20000, 400))
        rms100 = math.sqrt(float((steps[:, :100].sum(axis=1) ** 2).mean()))
        rms400 = math.sqrt(float((steps.sum(axis=1) ** 2).mean()))
        self.assertAlmostEqual(rms100, 10, delta=0.3)
        self.assertAlmostEqual(rms400, 20, delta=0.6)
        text = _lesson(3)
        self.assertIn("**20.1°**", text)
        self.assertIn("√100 = **10**", text)
        self.assertIn("√400 = **20**", text)


class Lesson4EngleGrangerTest(SimpleTestCase):
    def test_critical_values_match_mackinnon(self):
        from statsmodels.tsa.adfvalues import mackinnoncrit
        adf_5 = float(mackinnoncrit(N=1, regression="c", nobs=np.inf)[1])
        eg_5 = float(mackinnoncrit(N=2, regression="c", nobs=np.inf)[1])
        self.assertEqual(round(adf_5, 2), -2.86)
        self.assertEqual(round(eg_5, 2), -3.34)
        self.assertLess(-3.0, adf_5)          # −3.0 is below the ordinary ADF value: "significant" on the wrong table …
        self.assertGreater(-3.0, eg_5)        # … but above the Engle–Granger value: not significant on the right one
        self.assertTrue(-3.5 < -3.34)                                     # the quick check
        text = _lesson(4)
        self.assertIn("**−2.86**", text)
        self.assertIn("**−3.34**", text)

    def test_half_life_example(self):
        self.assertAlmostEqual(-math.log(2) / -0.1, 6.931, places=3)
        self.assertIn("**6.9 candles**", _lesson(4))
