# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for the hypothesis parser: valid hypothesis, ambiguous
# hypothesis, missing asset, missing timeframe.

from django.test import SimpleTestCase

from bot.research_lab.interpreter import suggest_spec, explain_evidence, INTERPRETER_NAME


class AssetExtractionTest(SimpleTestCase):

    def test_bitcoin_alias_resolves_to_btc_usdt(self):
        spec = suggest_spec("I believe Bitcoin tends to fall after a volume spike.")
        self.assertEqual(spec.asset, "BTC/USDT")
        self.assertNotIn("asset", spec.ambiguous_fields)

    def test_direct_symbol_mention_resolves(self):
        spec = suggest_spec("AVAX/USDT reverts after extreme moves.")
        self.assertEqual(spec.asset, "AVAX/USDT")

    def test_no_recognizable_asset_is_ambiguous(self):
        spec = suggest_spec("Some vague statement about the market.")
        self.assertIsNone(spec.asset)
        self.assertIn("asset", spec.ambiguous_fields)


class DirectionExtractionTest(SimpleTestCase):

    def test_fall_keyword_maps_to_negative(self):
        spec = suggest_spec("Bitcoin tends to fall after extreme volume.")
        self.assertEqual(spec.direction, "negative")

    def test_rise_keyword_maps_to_positive(self):
        spec = suggest_spec("Bitcoin tends to rise after extreme volume.")
        self.assertEqual(spec.direction, "positive")

    def test_no_direction_word_is_ambiguous(self):
        spec = suggest_spec("Bitcoin reacts to extreme volume somehow.")
        self.assertIsNone(spec.direction)
        self.assertIn("direction", spec.ambiguous_fields)


class HorizonExtractionTest(SimpleTestCase):

    def test_hours_phrase_extracted_directly(self):
        spec = suggest_spec("Bitcoin falls within 6 hours of a volume spike.")
        self.assertEqual(spec.target.get("horizon"), 6)

    def test_days_phrase_converted_to_candles(self):
        spec = suggest_spec("Bitcoin falls within 2 days of a volume spike.")
        self.assertEqual(spec.target.get("horizon"), 48)  # claude code changed: 2 days * 24 (this platform's only interval is 1h)

    def test_no_horizon_mentioned_is_ambiguous(self):
        spec = suggest_spec("Bitcoin falls after a volume spike.")
        self.assertIsNone(spec.target.get("horizon"))
        self.assertIn("target", spec.ambiguous_fields)


class AmbiguousHypothesisTest(SimpleTestCase):

    def test_fully_vague_hypothesis_marks_every_extractable_field_ambiguous(self):
        spec = suggest_spec("Something happens sometimes.")
        self.assertIn("asset", spec.ambiguous_fields)
        self.assertIn("direction", spec.ambiguous_fields)
        self.assertIn("target", spec.ambiguous_fields)

    def test_timeframe_is_always_ambiguous_never_guessed(self):
        """The interpreter never silently assumes 1h even when everything
        else resolves cleanly — timeframe always requires explicit
        confirmation per this module's own honesty rule."""
        spec = suggest_spec("Bitcoin falls within 6 hours of extreme volume.")
        self.assertIn("timeframe", spec.ambiguous_fields)

    def test_placeholder_never_claims_to_be_ai(self):
        self.assertNotIn("ai", INTERPRETER_NAME.lower().replace("rule_based", ""))
        self.assertIn("placeholder", INTERPRETER_NAME)


class ExplainEvidenceTest(SimpleTestCase):

    class _FakeExperiment:
        def __init__(self, hypothesis_text, statistical_results, verdict):
            self.hypothesis_text = hypothesis_text
            self.statistical_results = statistical_results
            self.verdict = verdict

    def test_explanation_only_uses_numbers_already_in_the_evidence(self):
        exp = self._FakeExperiment("BTC falls after volume spikes", {"ic": 0.081234, "block_permutation_p_value": 0.002}, "SUPPORTED")
        text = explain_evidence(exp)
        self.assertIn("0.0812", text)  # claude code changed: the exact stored IC value, formatted — never a different number
        self.assertIn("SUPPORTED", text)

    def test_no_evidence_produces_an_honest_empty_statement(self):
        exp = self._FakeExperiment("BTC falls after volume spikes", {}, "")
        text = explain_evidence(exp)
        self.assertIn("No statistical evidence", text)
