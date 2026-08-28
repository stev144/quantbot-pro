# claude code changed: new file — Hardening Phase 2 §4. End-to-end
# integration tests proving the full story the mission asks for: family
# created -> scope defined -> frozen -> scope immutable -> experiment
# references the frozen family -> a wrong/superseded result becomes a
# NEW experiment, never a mutated old one. Real DB (django.test.TestCase),
# matching this project's established convention.

from django.test import TestCase

from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.models import (
    FamilyAlreadyFrozenError,
    HypothesisFamily,
    ResearchExperiment,
    ResearchRecordIsImmutableError,
)
from bot.research_lab.trial_service import (
    RESEARCH_SCRIPT_USERNAME,
    freeze_family_before_testing,
    get_research_script_actor,
    record_research_trial,
)


class FullTrialLifecycleTest(TestCase):
    """The scenario Hardening Mission §4 describes end to end, using ONLY
    this module's public functions — not reaching into the ORM directly —
    so this test also proves the service's own API is sufficient, not
    just that the underlying models work in isolation."""

    def test_family_created_scoped_frozen_then_scope_is_immutable(self):
        family = freeze_family_before_testing(
            name="phase2f-orderbook", feature_family=["depth_imbalance_2pct", "depth_concentration"],
            assets=["BTC/USDT", "ETH/USDT"], venue="binance", timeframe="1h",
            horizons=["forward_return_1h", "forward_return_4h", "forward_return_12h"],
        )
        self.assertIsNotNone(family.frozen_at)
        self.assertEqual(family.n_hypotheses, 2 * 2 * 3)

        # claude code changed: THE property this whole service exists to
        # guarantee — freeze_family_before_testing() never hands back a
        # mutable-scope family, so this raise proves the return value
        # itself is already locked, not merely lockABLE.
        family.feature_family = family.feature_family + ["a_feature_added_after_seeing_results"]
        with self.assertRaises(FamilyAlreadyFrozenError):
            family.save()

    def test_adding_an_asset_after_freezing_fails(self):
        family = freeze_family_before_testing(
            name="asset-scope-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        family.assets = ["BTC/USDT", "ETH/USDT"]   # widening the asset set post-freeze
        with self.assertRaises(FamilyAlreadyFrozenError):
            family.save()

    def test_adding_a_horizon_after_freezing_fails(self):
        family = freeze_family_before_testing(
            name="horizon-scope-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        family.horizons = ["forward_return_1h", "forward_return_4h"]
        with self.assertRaises(FamilyAlreadyFrozenError):
            family.save()

    def test_cannot_record_a_trial_against_an_unfrozen_family(self):
        # claude code changed: proves record_research_trial() itself
        # enforces the ordering, independent of HypothesisFamily.save()'s
        # own guard — even a family constructed WITHOUT going through
        # freeze_family_before_testing() (e.g. a draft still being
        # designed) cannot have a trial recorded against it.
        draft = HypothesisFamily.objects.create(
            feature_family=["x"], assets=["BTC/USDT"], horizons=["forward_return_1h"],
        )
        self.assertIsNone(draft.frozen_at)
        with self.assertRaises(ValueError):
            record_research_trial(
                hypothesis_family=draft, hypothesis_text="does x predict y",
                dataset_identities=[], statistical_results={}, verdict="INCONCLUSIVE",
            )

    def test_experiment_references_the_frozen_family_and_carries_a_real_fingerprint(self):
        family = freeze_family_before_testing(
            name="fingerprint-test", feature_family=["depth_imbalance_2pct"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_4h"],
        )
        identity = DatasetIdentity(
            source="binance_futures_bookDepth_archive", symbol="BTC/USDT", venue="binance",
            timeframe="1h", start_date="2026-02-25", end_date="2026-08-23", row_count=495827,
        )
        experiment = record_research_trial(
            hypothesis_family=family, hypothesis_text="does order-book depth imbalance predict BTC returns",
            dataset_identities=[identity], statistical_results={"BTC/USDT": {"ic": 0.04}}, verdict="REJECTED",
            code_version="deadbeef",
        )
        self.assertEqual(experiment.hypothesis_family_id, family.pk)
        self.assertEqual(experiment.data_fingerprint, identity.fingerprint())
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertEqual(experiment.student.username, RESEARCH_SCRIPT_USERNAME)
        self.assertFalse(experiment.student.is_active)   # claude code changed: the system actor can never authenticate/log in

    def test_completed_trial_is_immutable_and_a_correction_creates_a_new_experiment(self):
        family = freeze_family_before_testing(
            name="immutability-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        original = record_research_trial(
            hypothesis_family=family, hypothesis_text="x", dataset_identities=[],
            statistical_results={"note": "first pass"}, verdict="REJECTED",
        )

        original.verdict = "SUPPORTED"   # attempted rewrite of a completed trial's conclusion
        with self.assertRaises(ResearchRecordIsImmutableError):
            original.save()

        with self.assertRaises(ResearchRecordIsImmutableError):
            original.delete()

        # claude code changed: the ONLY sanctioned way to "correct" a
        # trial — a NEW ResearchExperiment, linked via rerun_of, the
        # ORIGINAL (including its REJECTED verdict) remains queryable
        # forever, per Hardening Mission §15/Rule E ("never delete or
        # overwrite a failed experiment").
        corrected = ResearchExperiment.objects.create(
            student=get_research_script_actor(), hypothesis_text=original.hypothesis_text,
            hypothesis_family=family, rerun_of=original,
        )
        self.assertTrue(ResearchExperiment.objects.filter(pk=original.pk, verdict="REJECTED").exists())
        self.assertEqual(corrected.rerun_of_id, original.pk)

    def test_rerunning_the_same_declared_scope_reuses_the_frozen_family_not_a_duplicate(self):
        first = freeze_family_before_testing(
            name="idempotency-test", feature_family=["x", "y"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_4h"],
        )
        second = freeze_family_before_testing(
            name="idempotency-test", feature_family=["x", "y"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_4h"],
        )
        self.assertEqual(first.pk, second.pk)

    def test_a_genuinely_different_declared_scope_creates_a_new_family_not_a_silent_widen(self):
        first = freeze_family_before_testing(
            name="genuine-change-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_4h"],
        )
        second = freeze_family_before_testing(
            name="genuine-change-test", feature_family=["x", "y"], assets=["BTC/USDT"],   # a real new hypothesis, not a mutation of the first
            venue="binance", timeframe="1h", horizons=["forward_return_4h"],
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertTrue(HypothesisFamily.objects.filter(pk=first.pk, feature_family=["x"]).exists())   # the original is untouched


class DatasetIdentityChangeDetectionTest(TestCase):
    """Hardening Mission §5 — 'two researchers running the same experiment
    against different underlying datasets must NOT receive an
    indistinguishable experiment identity.'"""

    def _identity(self, **overrides):
        base = dict(source="binance_futures_bookDepth_archive", symbol="BTC/USDT", venue="binance",
                     timeframe="1h", start_date="2026-02-25", end_date="2026-08-23", row_count=495827)
        base.update(overrides)
        return DatasetIdentity(**base)

    def test_same_dataset_acquired_twice_produces_the_same_experiment_fingerprint(self):
        family = freeze_family_before_testing(
            name="fp-repro-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        exp_a = record_research_trial(family, "x", [self._identity()], {}, "INCONCLUSIVE")
        # a second, independent researcher re-running the exact same acquisition
        exp_b_identity = self._identity()
        self.assertEqual(exp_a.data_fingerprint, exp_b_identity.fingerprint())

    def test_a_different_row_count_produces_a_different_experiment_fingerprint(self):
        family = freeze_family_before_testing(
            name="fp-diff-test", feature_family=["x"], assets=["BTC/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        exp_a = record_research_trial(family, "x", [self._identity(row_count=495827)], {}, "INCONCLUSIVE")
        exp_b = record_research_trial(family, "x", [self._identity(row_count=400000)], {}, "INCONCLUSIVE")
        self.assertNotEqual(exp_a.data_fingerprint, exp_b.data_fingerprint)

    def test_multi_symbol_trial_fingerprint_changes_if_either_symbols_dataset_changes(self):
        family = freeze_family_before_testing(
            name="fp-multi-test", feature_family=["x"], assets=["BTC/USDT", "ETH/USDT"],
            venue="binance", timeframe="1h", horizons=["forward_return_1h"],
        )
        btc = self._identity(symbol="BTC/USDT", row_count=495827)
        eth_v1 = self._identity(symbol="ETH/USDT", row_count=495827)
        eth_v2 = self._identity(symbol="ETH/USDT", row_count=495828)   # one different snapshot

        exp_a = record_research_trial(family, "x", [btc, eth_v1], {}, "INCONCLUSIVE")
        exp_b = record_research_trial(family, "x", [btc, eth_v2], {}, "INCONCLUSIVE")
        self.assertNotEqual(exp_a.data_fingerprint, exp_b.data_fingerprint)
