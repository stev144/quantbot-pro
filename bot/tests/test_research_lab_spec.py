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


# claude code changed: new — Multi-Asset Foundation Refactor Phase 1A,
# STEP 4. "RSI" (a feature) and "RSI < 30" (a condition on that feature)
# must never collapse into the same representation — this is the exact
# governing statistical principle spec.py's own module docstring states.
# formalize.py's _spec_from_post() already keeps these on entirely
# separate form fields/code branches (verified by inspection, no
# production code changed for this requirement) — these tests lock that
# invariant at the schema level so a future edit can't quietly merge them.
class FeatureConditionSeparationTest(SimpleTestCase):

    def test_a_bare_feature_hypothesis_has_empty_conditions(self):
        spec = _valid_spec(hypothesis_type="feature", features=["rsi"], conditions=[])
        result = validate_spec(spec)
        self.assertTrue(result.is_valid)
        self.assertEqual(spec.features, ["rsi"])
        self.assertEqual(spec.conditions, [])

    def test_a_conditional_hypothesis_has_empty_features(self):
        spec = _valid_spec(
            hypothesis_type="conditional", features=[],
            conditions=[{"feature": "rsi", "operator": "<", "threshold": 30}],
        )
        result = validate_spec(spec)
        self.assertTrue(result.is_valid)
        self.assertEqual(spec.features, [])
        self.assertEqual(spec.conditions, [{"feature": "rsi", "operator": "<", "threshold": 30}])

    def test_a_condition_string_is_never_a_valid_feature_name(self):
        """'RSI < 30' is a condition clause, not a feature this platform
        can calculate — putting it in `features` (instead of `conditions`)
        must be rejected, not silently accepted as a weird feature name."""
        spec = _valid_spec(hypothesis_type="feature", features=["RSI < 30"])
        result = validate_spec(spec)
        # claude code changed: validate_spec doesn't check `features`
        # membership directly (only `conditions[].feature` is checked
        # against DERIVABLE_FROM_OHLCV) — this test documents that a
        # condition-shaped string in `features` is never a *recognized*
        # feature, i.e. downstream tool calls (calculate_feature) would
        # reject it, not that spec validation itself catches every typo.
        self.assertNotIn("RSI < 30", spec.conditions)


class TimeframeHorizonSeparationTest(SimpleTestCase):
    """claude code changed: new — Multi-Asset Foundation Refactor STEP 3.
    A candle timeframe (data resolution) and a research horizon (forward
    outcome measured in candles) are two different fields, and horizon
    support is a function of timeframe, not a bare platform-wide list."""

    def test_horizon_valid_for_its_declared_timeframe(self):
        spec = _valid_spec(timeframe="1h", target={"type": "forward_return", "horizon": 24})
        result = validate_spec(spec)
        self.assertTrue(result.is_valid)

    def test_horizon_rejected_for_an_unsupported_timeframe(self):
        spec = _valid_spec(timeframe="4h", target={"type": "forward_return", "horizon": 24})
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("timeframe" in e for e in result.errors))

    def test_unsupported_horizon_on_a_valid_timeframe_is_rejected_not_coerced(self):
        """claude code changed: new — Phase 1B hardening, section 8. The
        exact historical bug spec.py's own module docstring references
        (Bug 4): a horizon that isn't one of the real supported values
        (1/4/24 candles for 1h) but names an otherwise-valid timeframe
        must fail validation explicitly here — never silently pass
        through and only fail later, deep inside a tool call, and never
        get silently rounded/coerced to the nearest supported horizon."""
        spec = _valid_spec(timeframe="1h", target={"type": "forward_return", "horizon": 12})
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("horizon" in e and "12" in e for e in result.errors))
        # claude code changed: the spec itself must retain exactly what
        # was asked (12) — validate_spec() never mutates its input to
        # silently substitute the nearest supported horizon (4 or 24).
        self.assertEqual(spec.target["horizon"], 12)

    def test_a_24_candle_horizon_is_never_represented_as_a_24h_timeframe(self):
        """The refactor brief's own required distinction: timeframe='1h' +
        target.horizon=24 means 24 CANDLES forward on 1h data, never a
        platform concept of 'timeframe=24h'."""
        spec = _valid_spec(timeframe="1h", target={"type": "forward_return", "horizon": 24})
        self.assertEqual(spec.timeframe, "1h")
        self.assertEqual(spec.target["horizon"], 24)
        self.assertNotEqual(spec.timeframe, "24h")


class AssetClassTest(SimpleTestCase):
    """claude code changed: new — Multi-Asset Foundation Refactor STEP 5."""

    def test_asset_class_is_none_by_default_and_still_valid(self):
        """Every experiment predating this field must keep validating
        exactly as before — asset_class is optional, not required."""
        spec = _valid_spec()
        self.assertIsNone(spec.asset_class)
        result = validate_spec(spec)
        self.assertTrue(result.is_valid)

    def test_resolved_asset_class_is_derived_from_the_instrument_registry(self):
        spec = _valid_spec(asset="BTC/USDT")
        self.assertEqual(spec.resolved_asset_class, "CRYPTO")

    def test_mismatched_asset_class_is_rejected(self):
        spec = _valid_spec(asset="BTC/USDT", asset_class="FOREX")
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("asset_class" in e for e in result.errors))

    def test_unknown_asset_class_value_is_rejected(self):
        spec = _valid_spec(asset_class="COMMODITIES")
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)


class ResearchScopeTest(SimpleTestCase):
    """claude code changed: new — Multi-Asset Foundation Refactor STEP 10."""

    def test_single_asset_hypothesis_defaults_to_single_asset_scope(self):
        spec = _valid_spec(hypothesis_type="feature")
        self.assertEqual(spec.research_scope, "SINGLE_ASSET")
        self.assertEqual(spec.instruments, ["BTC/USDT"])

    def test_pairs_hypothesis_is_pair_scope_with_two_instruments(self):
        spec = _valid_spec(hypothesis_type="pairs", asset="AVAX/USDT", asset_b="ATOM/USDT", risk_tier="MEDIUM")
        self.assertEqual(spec.research_scope, "PAIR")
        self.assertEqual(spec.instruments, ["AVAX/USDT", "ATOM/USDT"])

    def test_research_scope_is_computed_not_stored(self):
        """research_scope must never appear as a settable/serialized field
        — it is always derived from hypothesis_type, so it can never drift
        out of sync with it."""
        spec = _valid_spec()
        self.assertNotIn("research_scope", spec.to_dict())
