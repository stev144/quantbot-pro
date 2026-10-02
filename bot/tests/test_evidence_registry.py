# claude code changed: new file — Post-CRRP Tasks 3–4 (evidence classification
# and dashboard protection). Proves (1) the registry's safety rules hold
# (unregistered → UNVERIFIED; CANONICAL results need complete provenance),
# (2) the governance report's classifications are what the registry returns,
# (3) the four protected pages can no longer render a historical "supported"
# verdict as a green badge. Synthetic research_data in a temp dir; no network.

import copy
import os
import shutil
import tempfile
from datetime import datetime, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from bot.research import evidence_registry as er
from bot.views import research_lab_data as rld


class RegistryRulesTests(SimpleTestCase):
    def test_unregistered_artifact_is_unverified_never_current(self):
        s = er.classify_artifact("some_new_result_nobody_registered.csv")
        self.assertEqual(s["class"], er.UNVERIFIED)
        self.assertFalse(s["is_current_evidence"])

    def test_governance_report_classifications(self):
        expect = {
            "permutation_test/DODO_USDT_FIDA_USDT/permutation_verdict.csv": er.INVALIDATED,
            "walk_forward/walk_forward_leaderboard.csv": er.INVALIDATED,
            "DODO_FIDA_equity_curve.csv": er.INVALIDATED,
            "portfolio_7pair/summary.csv": er.INVALIDATED,
            "pca_ic_validation.csv": er.INVALIDATED,
            "forex_pca_ic_validation.csv": er.INVALIDATED,
            "forex_cross_sectional_family_verdict.json": er.INVALIDATED,
            "cointegration_pairs.csv": er.SUPERSEDED,
            "BTC_USDT_observations.csv": er.SUPERSEDED,
            "forex_cointegration_pairs_mt5_58.csv": er.LEGACY_DESCRIPTIVE,
            "BTC_USDT_validated_features.csv": er.LEGACY_DESCRIPTIVE,
            "contagion_validation/contagion_validated_features_pooled.csv": er.LEGACY_DESCRIPTIVE,
            "frozen_candidate_registry_2026-09-20.json": er.LEGACY_DESCRIPTIVE,
            "scan_150_2026-09-30/universe_selection_150.json": er.UNVERIFIED,
        }
        for path, cls in expect.items():
            with self.subTest(path=path):
                self.assertEqual(er.classify_artifact(path)["class"], cls)

    def test_no_research_result_is_canonical_in_this_registry(self):
        # the reassessment's finding: no strategy has canonical evidence — only governance records may be CANONICAL
        for e in er.load_registry()["artifacts"] + er.load_registry()["governance_log_sections"]:
            if e["class"] == er.CANONICAL:
                self.assertEqual(e.get("kind"), "governance_record", e)

    def test_canonical_result_without_provenance_is_refused(self):
        data = copy.deepcopy(er.load_registry())
        data["artifacts"].append({"pattern": "x.csv", "class": "CANONICAL", "reason": "r"})
        with self.assertRaisesRegex(er.RegistryError, "lacks provenance"):
            er.validate_registry(data)
        data["artifacts"][-1]["provenance"] = {f: "MISSING" for f in data["rules"]["canonical_result_requires"]}
        with self.assertRaisesRegex(er.RegistryError, "lacks provenance"):
            er.validate_registry(data)                                   # MISSING is not provenance

    def test_invalid_class_or_missing_reason_is_refused(self):
        data = copy.deepcopy(er.load_registry())
        data["artifacts"].append({"pattern": "y.csv", "class": "PROBABLY_FINE", "reason": "r"})
        with self.assertRaisesRegex(er.RegistryError, "invalid evidence class"):
            er.validate_registry(data)
        data = copy.deepcopy(er.load_registry())
        data["artifacts"].append({"pattern": "z.csv", "class": "LEGACY_DESCRIPTIVE"})
        with self.assertRaisesRegex(er.RegistryError, "without a reason"):
            er.validate_registry(data)

    def test_provenance_is_never_invented(self):
        p = er.classify_artifact("forex_cross_sectional_family_verdict.json")["provenance"]
        self.assertEqual(p["data_fingerprint"], "1cb67b0993989aa807cfa6aaf2efb451f28e90dd47f0e3de88cf01f4cc39ca82")  # recorded by the artifact itself
        self.assertEqual(p["code_commit"], "MISSING")
        self.assertEqual(p["run_id"], "MISSING")

    def test_log_sections(self):
        self.assertEqual(er.classify_log_section("Pipeline-wide audit — zero-execution-lag …")["class"], er.INVALIDATED)
        self.assertEqual(er.classify_log_section("DODO_USDT / FIDA_USDT — REJECTED")["class"], er.LEGACY_DESCRIPTIVE)
        self.assertEqual(er.classify_log_section("Some future section")["class"], er.UNVERIFIED)

    def test_research_experiments(self):
        pre = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertEqual(er.classify_research_experiment(pre, "cointegration_pairs_research", "PARTIALLY_SUPPORTED")["class"], er.INVALIDATED)
        self.assertEqual(er.classify_research_experiment(pre, "continuous_feature_research", "REGIME_DEPENDENT")["class"], er.UNVERIFIED)
        post = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.assertEqual(er.classify_research_experiment(post, "cointegration_pairs_research", "PARTIALLY_SUPPORTED")["class"], er.UNVERIFIED)


LOG = """# Model governance log

## Pipeline-wide audit — zero-execution-lag assumption

**Date:** 2026-09-15
**Verdict:** VALIDATED — the strongest, most cross-pair-consistent result

Body text.

## Brand new unregistered section

**Date:** 2026-10-05
**Verdict:** ACCEPTED

Body.
"""


class ProtectedPagesTests(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "permutation_test", "A_USDT_B_USDT"))
        os.makedirs(os.path.join(self.dir, "walk_forward"))
        with open(os.path.join(self.dir, "model_governance_log.md"), "w", encoding="utf-8") as f:
            f.write(LOG)
        with open(os.path.join(self.dir, "permutation_test", "A_USDT_B_USDT", "permutation_verdict.csv"), "w") as f:
            f.write("win_rate,win_rate_percentile,sharpe_ratio,sharpe_ratio_percentile,entry_ic,real_entry_ic_meaningfully_positive,edge_appears_real\n"
                    "0.87,100,6.7,100,0.5,True,True\n")
        with open(os.path.join(self.dir, "walk_forward", "walk_forward_leaderboard.csv"), "w") as f:
            f.write("pair,oos_trades,oos_win_rate,oos_sharpe,oos_profit_factor,walk_forward_efficiency,passed\nA/B,10,0.8,2.0,3.0,0.9,True\n")
        self.patch = mock.patch.object(rld, "RESEARCH_DATA_DIR", self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_archive_never_shows_a_legacy_validated_verdict_as_supported(self):
        for url in ("/research/archive/", "/research/rejected/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
        r = self.client.get("/research/archive/")
        self.assertContains(r, "INVALIDATED")
        self.assertContains(r, "legacy verdict: STATISTICALLY SUPPORTED")
        self.assertNotContains(r, '<span class="badge pos">STATISTICALLY SUPPORTED</span>')
        self.assertContains(r, "UNVERIFIED")                               # the unregistered section is never current

    def test_robustness_never_shows_a_green_edge_yes(self):
        r = self.client.get("/research/robustness/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "EVIDENCE: INVALIDATED")
        self.assertNotContains(r, '<span class="badge pos">YES</span>')
        self.assertContains(r, "legacy: yes")

    def test_pairs_performance_verdict_block_is_demoted(self):
        ev = {"equity": er.classify_artifact("A_B_equity_curve.csv"),
              "permutation": er.classify_artifact("permutation_test/X/permutation_verdict.csv"),
              "walk_forward": er.classify_artifact("walk_forward/walk_forward_leaderboard.csv"),
              "cointegration": er.classify_artifact("cointegration_pairs.csv")}
        html = render_to_string("pairs_performance.html", {
            "pairs": ["A_B"], "pair": "A_B",
            "performance": {"available": True, "prefix": "A_B", "long_name": "A_USDT_B_USDT", "summary": {},
                            "permutation": {"edge_appears_real": True}, "walk_forward": {"passed": True},
                            "cointegration": None, "equity_curve": [], "drawdown_curve": [], "trades": [], "evidence": ev}})
        self.assertIn("EVIDENCE: INVALIDATED", html)
        self.assertNotIn("EDGE APPEARS REAL</span>", html)
        self.assertNotIn("WALK-FORWARD PASSED</span>", html)
        self.assertIn("legacy permutation: edge_appears_real=yes", html)

    def test_research_lab_history_and_results_show_evidence(self):
        from bot.research_lab.models import ResearchExperiment
        user = get_user_model().objects.create_user("researcher", password="pw-123456")
        exp = ResearchExperiment.objects.create(student=user, hypothesis_text="pairs test", verdict="PARTIALLY_SUPPORTED",
                                                capability_id="cointegration_pairs_research")
        ResearchExperiment.objects.filter(id=exp.id).update(created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.client.login(username="researcher", password="pw-123456")
        r = self.client.get("/research-lab/history/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "INVALIDATED")
        self.assertContains(r, "not current evidence")
