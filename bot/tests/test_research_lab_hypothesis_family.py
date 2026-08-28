# claude code changed: new file — Hardening Mission Section 4/25. Tests
# the concrete code enforcement added to bot/research_lab/models.py:
# HypothesisFamily's freeze-then-lock and ResearchExperiment's
# terminal-status append-only guarantee. Real DB (django.test.TestCase),
# matching this project's established convention for research_lab model
# tests (test_research_lab_orchestrator.py's own precedent).

from django.contrib.auth.models import User
from django.test import TestCase

from bot.research_lab.data_fingerprint import DatasetIdentity, fingerprint_dataset
from bot.research_lab.models import (
    FamilyAlreadyFrozenError,
    HypothesisFamily,
    ResearchExperiment,
    ResearchRecordIsImmutableError,
)


def _make_user(username="researcher"):
    return User.objects.create_user(username=username, password="x")


class HypothesisFamilyFreezeTest(TestCase):

    def test_scope_is_freely_editable_before_freezing(self):
        family = HypothesisFamily.objects.create(
            name="derivatives-phase2e", feature_family=["funding_rate"], assets=["BTC/USDT"],
            horizons=["forward_return_4h"], venue="binance", timeframe="1h",
        )
        family.feature_family = ["funding_rate", "oi_change_1h"]   # still a draft — allowed
        family.save()
        family.refresh_from_db()
        self.assertEqual(family.feature_family, ["funding_rate", "oi_change_1h"])
        self.assertIsNone(family.frozen_at)

    def test_freeze_locks_the_scope_permanently(self):
        family = HypothesisFamily.objects.create(
            feature_family=["funding_rate", "oi_change_1h"], assets=["BTC/USDT", "ETH/USDT"],
            horizons=["forward_return_1h", "forward_return_4h", "forward_return_12h"],
            venue="binance", timeframe="1h",
        )
        family.freeze()
        self.assertIsNotNone(family.frozen_at)
        self.assertEqual(family.n_hypotheses, 2 * 2 * 3)   # exactly Phase 2E's real 12-test family shape

        family.feature_family = ["funding_rate", "oi_change_1h", "oi_change_24h"]   # widening after freeze
        with self.assertRaises(FamilyAlreadyFrozenError):
            family.save()

    def test_freeze_is_idempotent(self):
        family = HypothesisFamily.objects.create(feature_family=["x"], assets=["BTC/USDT"], horizons=["forward_return_1h"])
        family.freeze()
        first_frozen_at = family.frozen_at
        family.freeze()   # calling again must not error or re-stamp a new timestamp via a raise
        self.assertEqual(family.frozen_at, first_frozen_at)

    def test_frozen_family_cannot_be_deleted(self):
        family = HypothesisFamily.objects.create(feature_family=["x"], assets=["BTC/USDT"], horizons=["forward_return_1h"])
        family.freeze()
        with self.assertRaises(ResearchRecordIsImmutableError):
            family.delete()

    def test_non_scope_fields_remain_editable_after_freezing(self):
        # claude code changed: freezing locks the HYPOTHESIS SCOPE, not the
        # whole row — a display name isn't part of what "family-wide FDR
        # was computed against", so it may still be edited (e.g. to fix a
        # typo) without that being a methodology change.
        family = HypothesisFamily.objects.create(name="typo", feature_family=["x"], assets=["BTC/USDT"], horizons=["forward_return_1h"])
        family.freeze()
        family.name = "phase2e-derivatives-family"
        family.save()   # must not raise
        family.refresh_from_db()
        self.assertEqual(family.name, "phase2e-derivatives-family")


class ResearchExperimentAppendOnlyTest(TestCase):

    def _make_experiment(self, family=None):
        return ResearchExperiment.objects.create(
            student=_make_user(), hypothesis_text="does X predict Y", hypothesis_family=family,
        )

    def test_non_terminal_experiment_is_freely_editable(self):
        exp = self._make_experiment()
        exp.status = "RUNNING"
        exp.save()   # must not raise — PENDING/PLANNED/RUNNING are legitimately mutable
        exp.refresh_from_db()
        self.assertEqual(exp.status, "RUNNING")

    def test_completed_experiment_cannot_be_modified_again(self):
        exp = self._make_experiment()
        exp.status = "COMPLETED"
        exp.verdict = "REJECTED"
        exp.save()   # the ONE sanctioned transition into terminal state — must succeed

        exp.ai_interpretation = "actually let's call this SUPPORTED"   # attempted rewrite of history
        exp.verdict = "SUPPORTED"
        with self.assertRaises(ResearchRecordIsImmutableError):
            exp.save()

    def test_failed_experiment_is_also_immutable(self):
        exp = self._make_experiment()
        exp.status = "FAILED"
        exp.error_message = "tool crashed"
        exp.save()

        exp.status = "COMPLETED"   # attempting to quietly reclassify a failure as a success
        with self.assertRaises(ResearchRecordIsImmutableError):
            exp.save()

    def test_no_experiment_can_ever_be_deleted(self):
        exp = self._make_experiment()
        with self.assertRaises(ResearchRecordIsImmutableError):
            exp.delete()

        exp.status = "COMPLETED"
        exp.save()
        with self.assertRaises(ResearchRecordIsImmutableError):
            exp.delete()

    def test_a_wrong_result_must_become_a_new_experiment_via_rerun_of(self):
        original = self._make_experiment()
        original.status = "COMPLETED"
        original.verdict = "REJECTED"
        original.save()

        corrected = ResearchExperiment.objects.create(
            student=original.student, hypothesis_text=original.hypothesis_text, rerun_of=original,
        )
        self.assertEqual(corrected.rerun_of, original)
        self.assertEqual(ResearchExperiment.objects.filter(rerun_of=original).count(), 1)

    def test_family_with_a_linked_experiment_cannot_be_deleted(self):
        family = HypothesisFamily.objects.create(feature_family=["oi_change_1h"], assets=["BTC/USDT"], horizons=["forward_return_4h"])
        self._make_experiment(family=family)
        with self.assertRaises(Exception):   # django.db.models.ProtectedError, via on_delete=PROTECT
            family.delete()


class DataFingerprintTest(TestCase):

    def test_same_identity_produces_the_same_fingerprint(self):
        a = fingerprint_dataset("binance_futures_bookDepth_archive", "BTC/USDT", "binance", "1h", "2026-02-25", "2026-08-23", row_count=4342)
        b = fingerprint_dataset("binance_futures_bookDepth_archive", "BTC/USDT", "binance", "1h", "2026-02-25", "2026-08-23", row_count=4342)
        self.assertEqual(a, b)

    def test_different_row_count_produces_a_different_fingerprint(self):
        # claude code changed: THE property this whole mechanism exists for
        # — the same nominal window re-acquired later, with a different
        # actual row count (e.g. an exchange backfilled or corrected a
        # day), must be detectably a different dataset.
        a = fingerprint_dataset("binance_futures_bookDepth_archive", "BTC/USDT", "binance", "1h", "2026-02-25", "2026-08-23", row_count=4342)
        b = fingerprint_dataset("binance_futures_bookDepth_archive", "BTC/USDT", "binance", "1h", "2026-02-25", "2026-08-23", row_count=4341)
        self.assertNotEqual(a, b)

    def test_stored_on_the_experiment_round_trips(self):
        fp = fingerprint_dataset("binance_spot_klines", "BTC/USDT", "binance", "1h", "2026-02-25", "2026-08-23", row_count=4342)
        exp = ResearchExperiment.objects.create(student=_make_user(), hypothesis_text="x", data_fingerprint=fp)
        exp.refresh_from_db()
        self.assertEqual(exp.data_fingerprint, fp)
