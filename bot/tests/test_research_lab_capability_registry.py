# claude code changed: new file — Advanced Quant Research Capability
# Architecture. Tests the registry's own internal consistency (section 18:
# "every capability has a valid definition", "missing engines cannot be
# exposed", "unsupported capabilities fail closed").

from django.test import SimpleTestCase

from bot.research_lab.capability_registry import (
    RESEARCH_CAPABILITIES, SUBSCRIPTION_TIERS, ENGINE_STATUS_CHOICES,
    IMPLEMENTED_AND_READY, get_capability, capability_for_hypothesis_type,
    list_by_category,
)
from bot.research_lab.spec import RISK_TIERS, HYPOTHESIS_TYPES


class RegistryValidityTest(SimpleTestCase):

    def test_every_capability_has_a_valid_definition(self):
        for capability_id, capability in RESEARCH_CAPABILITIES.items():
            self.assertEqual(capability.id, capability_id, capability_id)
            self.assertTrue(capability.name, capability_id)
            self.assertTrue(capability.description, capability_id)
            self.assertTrue(capability.category, capability_id)
            self.assertIn(capability.required_tier, SUBSCRIPTION_TIERS, capability_id)
            self.assertIn(capability.risk_tier, RISK_TIERS, capability_id)
            self.assertIn(capability.compute_tier, ("LOW", "MEDIUM", "HIGH"), capability_id)
            self.assertTrue(capability.backing_engine, capability_id)
            self.assertIn(capability.engine_status, ENGINE_STATUS_CHOICES, capability_id)
            self.assertIsInstance(capability.has_tests, bool, capability_id)

    def test_every_hypothesis_type_that_maps_to_a_capability_is_a_real_spec_hypothesis_type(self):
        for capability in RESEARCH_CAPABILITIES.values():
            if capability.hypothesis_type is not None:
                self.assertIn(capability.hypothesis_type, HYPOTHESIS_TYPES, capability.id)

    def test_operationally_ready_is_true_only_for_implemented_and_ready(self):
        for capability in RESEARCH_CAPABILITIES.values():
            self.assertEqual(capability.operationally_ready, capability.engine_status == IMPLEMENTED_AND_READY, capability.id)


class MissingEnginesCannotBeExposedTest(SimpleTestCase):
    """claude code changed: spot-checks the exact capabilities the Phase A
    audit found real gaps in — if someone flips one of these to READY
    without actually doing the underlying work, this test catches it."""

    def test_kalman_is_not_ready(self):
        self.assertFalse(RESEARCH_CAPABILITIES["kalman_dynamic_hedge_ratio"].operationally_ready)

    def test_contagion_is_not_ready(self):
        self.assertFalse(RESEARCH_CAPABILITIES["contagion_divergence_research"].operationally_ready)

    def test_walk_forward_and_permutation_are_blocked_by_dependency(self):
        self.assertEqual(RESEARCH_CAPABILITIES["walk_forward_validation"].engine_status, "BLOCKED_BY_DEPENDENCY")
        self.assertEqual(RESEARCH_CAPABILITIES["permutation_robustness_testing"].engine_status, "BLOCKED_BY_DEPENDENCY")

    def test_cross_sectional_and_feature_stability_decay_are_not_implemented(self):
        for capability_id in ("cross_sectional_research", "feature_stability_research", "feature_decay_research"):
            self.assertEqual(RESEARCH_CAPABILITIES[capability_id].engine_status, "NOT_IMPLEMENTED", capability_id)

    def test_cointegration_pairs_research_is_ready(self):
        """The one capability this pass actually finished wiring end to end."""
        self.assertTrue(RESEARCH_CAPABILITIES["cointegration_pairs_research"].operationally_ready)


class HypothesisTypeMappingTest(SimpleTestCase):

    def test_feature_conditional_pairs_map_to_the_right_capability(self):
        self.assertEqual(capability_for_hypothesis_type("feature").id, "continuous_feature_research")
        self.assertEqual(capability_for_hypothesis_type("conditional").id, "conditional_event_research")
        self.assertEqual(capability_for_hypothesis_type("pairs").id, "cointegration_pairs_research")

    def test_unknown_hypothesis_type_fails_closed_to_none(self):
        self.assertIsNone(capability_for_hypothesis_type("not_a_real_type"))


class LookupHelpersTest(SimpleTestCase):

    def test_get_capability_returns_none_for_unknown_id(self):
        self.assertIsNone(get_capability("not_a_real_capability"))

    def test_get_capability_returns_the_real_definition(self):
        self.assertEqual(get_capability("cointegration_pairs_research").name, "Cointegration & Pairs Research")

    def test_list_by_category_covers_every_capability_exactly_once(self):
        grouped = list_by_category()
        total = sum(len(v) for v in grouped.values())
        self.assertEqual(total, len(RESEARCH_CAPABILITIES))
