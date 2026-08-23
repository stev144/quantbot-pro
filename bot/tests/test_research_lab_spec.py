# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for the hypothesis spec/schema layer: valid hypothesis,
# ambiguous hypothesis, missing asset, missing timeframe.

from django.test import SimpleTestCase

from bot.research_lab.spec import ResearchSpec, validate_spec


def _valid_spec(**overrides):
    defaults = dict(
        hypothesis_text="Bitcoin tends to fall after extreme volume spikes.",
        asset="BTC/USDT",
        timeframe="1h",
        direction="negative",
        target={"type": "forward_return", "horizon": 24},
        risk_tier="LOW",
    )
    defaults.update(overrides)
    return ResearchSpec(**defaults)


class ValidSpecTest(SimpleTestCase):

    def test_fully_specified_spec_is_valid(self):
        result = validate_spec(_valid_spec())
        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, [])


class MissingFieldTest(SimpleTestCase):

    def test_missing_asset_is_invalid(self):
        result = validate_spec(_valid_spec(asset=None))
        self.assertFalse(result.is_valid)
        self.assertTrue(any("asset" in e for e in result.errors))

    def test_missing_timeframe_is_invalid(self):
        result = validate_spec(_valid_spec(timeframe=None))
        self.assertFalse(result.is_valid)
        self.assertTrue(any("timeframe" in e for e in result.errors))

    def test_unsupported_asset_is_invalid(self):
        result = validate_spec(_valid_spec(asset="DOGE_TO_THE_MOON/USDT"))  # claude code changed: not in the real 20-symbol universe
        self.assertFalse(result.is_valid)

    def test_unsupported_timeframe_is_invalid(self):
        result = validate_spec(_valid_spec(timeframe="4h"))  # claude code changed: this repo only ever fetches 1h candles
        self.assertFalse(result.is_valid)

    def test_missing_target_horizon_is_invalid(self):
        result = validate_spec(_valid_spec(target={"type": "forward_return"}))
        self.assertFalse(result.is_valid)

    def test_negative_target_horizon_is_invalid(self):
        result = validate_spec(_valid_spec(target={"type": "forward_return", "horizon": -5}))
        self.assertFalse(result.is_valid)


class AmbiguousFieldTest(SimpleTestCase):

    def test_ambiguous_field_makes_spec_invalid_even_with_a_value_present(self):
        """A placeholder value sitting in an ambiguous field is not evidence
        the field was understood — the ambiguity flag must win."""
        spec = _valid_spec(asset="BTC/USDT", ambiguous_fields=["asset"])
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertIn("asset", result.unresolved_ambiguous_fields)

    def test_unknown_ambiguous_field_name_is_ignored_not_trusted(self):
        spec = _valid_spec(ambiguous_fields=["not_a_real_field"])
        result = validate_spec(spec)
        self.assertTrue(result.is_valid)  # claude code changed: garbage field names don't block an otherwise-valid spec
        self.assertEqual(result.unresolved_ambiguous_fields, [])


class RoundTripTest(SimpleTestCase):

    def test_to_dict_from_dict_round_trip_preserves_every_field(self):
        # claude code changed: conditions is now List[Dict] ({"feature",
        # "operator", "threshold"}), not List[str] — Conditional Hypothesis
        # Integrity fix. Fixture updated to the real current schema; the
        # test's actual assertion (round-trip preserves every field,
        # including conditions) is unchanged.
        spec = _valid_spec(features=["volume_zscore"], conditions=[{"feature": "volume", "operator": ">", "threshold": 2.0}])
        restored = ResearchSpec.from_dict(spec.to_dict())
        self.assertEqual(spec.to_dict(), restored.to_dict())
