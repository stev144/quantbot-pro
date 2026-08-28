# claude code changed: new file — Phase 2D/Research Lab Completion, Step 5.
# Coverage for bot/research_lab/evidence_interface.py: read-only query
# functions over the EXISTING ResearchExperiment table. Experiments are
# constructed directly via the ORM (no real data/*.csv needed, no research
# pipeline run) — these are query-logic tests, not pipeline tests.

from django.contrib.auth.models import User
from django.test import TestCase

from bot.research_lab import evidence_interface as ei
from bot.research_lab.models import ResearchExperiment


def _make_experiment(user, verdict, feature=None, asset="BTC/USDT", hypothesis_type="feature", capability_id="", conditions=None):
    spec = {"asset": asset, "hypothesis_type": hypothesis_type}
    if feature and hypothesis_type == "feature":
        spec["features"] = [feature]
    if conditions:
        spec["conditions"] = conditions
    return ResearchExperiment.objects.create(
        student=user, hypothesis_text="test", status="COMPLETED",
        verdict=verdict, structured_spec=spec, capability_id=capability_id,
    )


class FeatureNameResolutionTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei1", password="x")

    def test_resolves_from_features_list_for_feature_hypothesis(self):
        exp = _make_experiment(self.user, "SUPPORTED", feature="rsi")
        self.assertEqual(ei._feature_name(exp), "rsi")

    def test_resolves_from_conditions_for_conditional_hypothesis(self):
        exp = _make_experiment(self.user, "SUPPORTED", hypothesis_type="conditional",
                                conditions=[{"feature": "atr_ratio", "operator": ">", "threshold": 1.5}])
        self.assertEqual(ei._feature_name(exp), "atr_ratio")

    def test_none_for_pairs_hypothesis_with_no_single_feature(self):
        exp = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="COMPLETED", verdict="SUPPORTED",
            structured_spec={"asset": "BTC/USDT", "asset_b": "ETH/USDT", "hypothesis_type": "pairs"},
        )
        self.assertIsNone(ei._feature_name(exp))


class SupportedRejectedFeaturesTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei2", password="x")

    def test_list_supported_features_only_includes_survived_verdicts(self):
        _make_experiment(self.user, "SUPPORTED", feature="rsi")
        _make_experiment(self.user, "PARTIALLY_SUPPORTED", feature="macd")
        _make_experiment(self.user, "REJECTED", feature="volume_ratio")

        supported = ei.list_supported_features()
        names = {row["feature"] for row in supported}
        self.assertEqual(names, {"rsi", "macd"})

    def test_list_rejected_features_excludes_invalid_research(self):
        _make_experiment(self.user, "REJECTED", feature="rsi")
        _make_experiment(self.user, "INCONCLUSIVE", feature="atr")
        _make_experiment(self.user, "INVALID_RESEARCH", feature="adx")   # claude code changed: never validly tested, must NOT appear here

        rejected = ei.list_rejected_features()
        names = {row["feature"] for row in rejected}
        self.assertEqual(names, {"rsi", "atr"})

    def test_incomplete_experiments_are_excluded(self):
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="PLANNED", verdict="",
            structured_spec={"asset": "BTC/USDT", "features": ["rsi"]},
        )
        self.assertEqual(ei.list_supported_features(), [])


class UntestableExperimentsTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei3", password="x")

    def test_lists_invalid_and_insufficient_data_verdicts(self):
        _make_experiment(self.user, "INVALID_RESEARCH", feature="rsi")
        _make_experiment(self.user, "INSUFFICIENT_DATA", feature="macd")
        _make_experiment(self.user, "SUPPORTED", feature="atr")

        result = ei.list_untestable_experiments()
        self.assertEqual(len(result), 2)
        self.assertEqual({r["verdict"] for r in result}, {"INVALID_RESEARCH", "INSUFFICIENT_DATA"})


class CrossSymbolConsistencyTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei4", password="x")

    def test_replicates_true_when_supported_on_every_tested_asset(self):
        _make_experiment(self.user, "SUPPORTED", feature="rsi", asset="BTC/USDT")
        _make_experiment(self.user, "PARTIALLY_SUPPORTED", feature="rsi", asset="ETH/USDT")

        result = ei.cross_symbol_consistency("rsi")
        self.assertEqual(result["n_assets_tested"], 2)
        self.assertTrue(result["replicates_across_assets"])

    def test_replicates_false_when_verdicts_disagree_across_assets(self):
        # claude code changed: THE case Phase 2D's own brief named
        # explicitly — supported on one asset, rejected on another, must
        # never be described as "replicates."
        _make_experiment(self.user, "SUPPORTED", feature="rsi", asset="BTC/USDT")
        _make_experiment(self.user, "REJECTED", feature="rsi", asset="ETH/USDT")

        result = ei.cross_symbol_consistency("rsi")
        self.assertFalse(result["replicates_across_assets"])

    def test_none_not_false_when_feature_was_never_tested(self):
        result = ei.cross_symbol_consistency("nonexistent_feature_xyz")
        self.assertIsNone(result["replicates_across_assets"])   # claude code changed: "unknown," never silently "failed"
        self.assertEqual(result["n_assets_tested"], 0)


class UntestedCapabilitiesTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei5", password="x")

    def test_a_capability_never_run_as_an_experiment_is_listed(self):
        result = ei.list_untested_capabilities()
        ids = {r["capability_id"] for r in result}
        self.assertIn("crypto_trade_flow_research", ids)   # claude code changed: real capability, genuinely never run as a Research Lab experiment (Phase 2B/2C/2D's own testing was all ad-hoc scripts, never through this pipeline)

    def test_a_capability_that_HAS_been_run_is_excluded(self):
        _make_experiment(self.user, "SUPPORTED", feature="rsi", capability_id="continuous_feature_research")
        result = ei.list_untested_capabilities()
        ids = {r["capability_id"] for r in result}
        self.assertNotIn("continuous_feature_research", ids)


class ExperimentEvidenceSummaryTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="ei6", password="x")

    def test_returns_full_bundle_for_a_real_experiment(self):
        # claude code changed: was _make_experiment() (creates the row
        # already COMPLETED) followed by a second save() to add
        # warnings/ai_interpretation afterward. Hardening Phase 2 made
        # ResearchExperiment append-only once terminal (matching
        # orchestrator.py's real lifecycle: every evidence field is set
        # BEFORE the single save into COMPLETED, never after) — a second
        # save on an already-COMPLETED row now correctly raises
        # ResearchRecordIsImmutableError. Fixed by constructing this one
        # experiment with every field set in its single create() call,
        # not by weakening the new append-only guarantee.
        exp = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="COMPLETED",
            verdict="SUPPORTED", structured_spec={"asset": "BTC/USDT", "hypothesis_type": "feature", "features": ["rsi"]},
            warnings=["small sample size"], ai_interpretation="some explanation",
        )

        summary = ei.experiment_evidence_summary(exp.id)
        self.assertEqual(summary["experiment_id"], str(exp.id))
        self.assertEqual(summary["verdict"], "SUPPORTED")
        self.assertEqual(summary["warnings"], ["small sample size"])
        self.assertEqual(summary["ai_interpretation"], "some explanation")

    def test_returns_none_for_unknown_id(self):
        import uuid
        self.assertIsNone(ei.experiment_evidence_summary(uuid.uuid4()))
