# ============================================================
# bot/tests/test_deep_health_check.py
# claude code changed: new file — Forensic Audit & Deep Health Check
# Hardening mission, Milestone A item 1. No test coverage existed for
# deep_health_check.py before this. Covers the one concrete defect a
# forensic audit found: LayerRunner's reflection-based blind function-
# calling (see deep_health_check.py's own LIVE_EXCHANGE_MODULE_PATTERNS
# comment) reached bot.core.bot_runner during a real run and fired an
# actual signed, authenticated HTTP request to Binance's live API using
# whatever real credentials happened to be in the environment — it
# failed harmlessly only because no real key was configured on the
# machine that surfaced it. This is a regression guard against that
# exact class of incident recurring silently.
# ============================================================

from unittest.mock import patch

from django.test import SimpleTestCase

from bot.management.commands.deep_health_check import (
    should_exclude_module, discover_all_bot_modules, LIVE_EXCHANGE_MODULE_PATTERNS,
    GREEN_SEVERITY, YELLOW_SEVERITY, ORANGE_SEVERITY, RED_SEVERITY,
    HealthFinding, FunctionResult, DriftResult,
    compute_overall_severity, _finding_from_function_result, _finding_from_drift_result,
    check_universe_dynamism, check_governance_boundary,
    check_dataset_fingerprint_coverage, check_reconciliation_gate,
    UNGOVERNED_RESEARCH_ENGINES, FINGERPRINT_COVERAGE_ENGINES,
    check_forex_dataset_freshness, check_forex_dataset_quality,
    check_forex_capability_governance, check_forex_dataset_fingerprint_reproducibility,
    check_forex_cross_sectional_research_wiring,
)


class LiveExchangeModuleExclusionTest(SimpleTestCase):
    """Every module confirmed by the forensic audit to make a real
    authenticated exchange call, or to trigger an uncontrolled heavy
    real network operation, must be excluded from deep_health_check's
    reflection-based auditing."""

    DANGEROUS_MODULES = [
        "bot.core.bot_runner",           # the exact module involved in the real incident
        "bot.engines.execution_engine",
        "bot.engines.order_manager",
        "bot.engines.binance_adapter",
        "bot.engines.kraken_adapter",
        "bot.engines.position_tracker",
        "bot.engines.market_data",
        "bot.engines.trade_data",        # the exact module that flooded real aggTrades traffic in the same incident
        "bot.universe_selector",
        "bot.data_fetcher",
        "bot.fetch_all_symbols",         # download_all_symbols() — confirmed live by a real user report of a full crypto-universe download during a routine run
        "bot.forex_data_fetcher",        # download_all_forex_symbols() — same shape, same risk, for the Forex universe
    ]

    def test_every_known_dangerous_module_is_excluded(self):
        for module_path in self.DANGEROUS_MODULES:
            self.assertTrue(should_exclude_module(module_path), f"{module_path} must be excluded — see LIVE_EXCHANGE_MODULE_PATTERNS")

    def test_dangerous_modules_never_appear_in_the_real_discovery_list(self):
        """The actual list LayerRunner iterates over in a real run —
        proves the exclusion is wired into the real discovery path, not
        just satisfied in isolation by should_exclude_module()."""
        discovered = discover_all_bot_modules()
        leaked = [m for m in self.DANGEROUS_MODULES if m in discovered]
        self.assertEqual(leaked, [], f"these dangerous modules leaked into the real audit discovery list: {leaked}")

    def test_price_validator_is_not_excluded(self):
        """Deliberately NOT in the exclusion list — its own docstring
        and implementation confirm exactly one lightweight public
        fetch_ticker() call, no balances, no authentication, no
        pagination. Proves the fix is evidence-scoped, not a blanket
        'exclude anything networked' overreach."""
        self.assertFalse(should_exclude_module("bot.engines.price_validator"))

    def test_prefix_matching_does_not_over_match_a_similarly_named_module(self):
        """should_exclude_module() must match on a real dotted-path
        prefix boundary (exact match or followed by '.'), never a bare
        substring — otherwise a legitimate module merely sharing a name
        fragment (e.g. a hypothetical bot.engines.market_data_helper)
        would be silently, incorrectly excluded from auditing too."""
        self.assertFalse(should_exclude_module("bot.engines.market_data_helper"))
        self.assertFalse(should_exclude_module("bot.engines.order_manager_utils"))

    def test_live_exchange_patterns_set_matches_what_this_test_asserts(self):
        """Guards against the two lists silently drifting apart — if a
        module is ever removed from LIVE_EXCHANGE_MODULE_PATTERNS
        without updating this test, this fails loudly instead of the
        exclusion silently weakening."""
        self.assertEqual(set(LIVE_EXCHANGE_MODULE_PATTERNS), set(self.DANGEROUS_MODULES))


class OverallSeverityRuleTest(SimpleTestCase):
    # claude code changed: new — Forensic Audit Milestone B. Direct unit
    # test of the mission's own explicit rule: a critical RED must never
    # be hidden behind any number of passing GREENs via averaging.

    def _finding(self, severity):
        return HealthFinding(component="x", check="x", severity=severity,
                              evidence="", expected="", actual="", impact="")

    def test_one_red_among_fifty_greens_is_red_overall(self):
        findings = [self._finding(GREEN_SEVERITY) for _ in range(50)] + [self._finding(RED_SEVERITY)]
        self.assertEqual(compute_overall_severity(findings), RED_SEVERITY)

    def test_no_findings_is_green(self):
        self.assertEqual(compute_overall_severity([]), GREEN_SEVERITY)

    def test_severity_ranking_order(self):
        self.assertEqual(compute_overall_severity([self._finding(YELLOW_SEVERITY), self._finding(GREEN_SEVERITY)]), YELLOW_SEVERITY)
        self.assertEqual(compute_overall_severity([self._finding(ORANGE_SEVERITY), self._finding(YELLOW_SEVERITY)]), ORANGE_SEVERITY)
        self.assertEqual(compute_overall_severity([self._finding(RED_SEVERITY), self._finding(ORANGE_SEVERITY)]), RED_SEVERITY)


class FunctionResultToFindingMappingTest(SimpleTestCase):
    # claude code changed: new — proves existing Layer 2/3 results map
    # into the new severity model correctly, without altering the
    # original FunctionResult/overall_status fields at all.

    def _base_kwargs(self):
        return dict(module_path="bot.fake_module", class_name="FakeClass",
                    function_name="fake_fn", is_method=True)

    def test_layer2_crash_maps_to_red(self):
        r = FunctionResult(**self._base_kwargs(), layer2_error="ValueError: boom", overall_status="FAIL")
        finding = _finding_from_function_result(r)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.severity, RED_SEVERITY)
        self.assertIn("boom", finding.evidence)

    def test_layer3_wrong_output_maps_to_red(self):
        r = FunctionResult(**self._base_kwargs(), layer3_tested=True, layer3_passed=False,
                            layer3_message="WRONG", layer3_expected="0.5", layer3_actual="0.9",
                            overall_status="FAIL")
        finding = _finding_from_function_result(r)
        self.assertEqual(finding.severity, RED_SEVERITY)
        self.assertEqual(finding.expected, "0.5")
        self.assertEqual(finding.actual, "0.9")

    def test_missing_layer3_test_maps_to_yellow(self):
        r = FunctionResult(**self._base_kwargs(), layer3_tested=False, overall_status="WARN")
        finding = _finding_from_function_result(r)
        self.assertEqual(finding.severity, YELLOW_SEVERITY)

    def test_pass_produces_no_finding(self):
        r = FunctionResult(**self._base_kwargs(), layer3_tested=True, layer3_passed=True, overall_status="PASS")
        self.assertIsNone(_finding_from_function_result(r))

    def test_skip_produces_no_finding(self):
        r = FunctionResult(**self._base_kwargs(), overall_status="SKIP")
        self.assertIsNone(_finding_from_function_result(r))


class DriftResultToFindingMappingTest(SimpleTestCase):

    def _drift(self, severity):
        return DriftResult(metric_name="adr_dollars", module="RangeAnalytics",
                            current_value=100.0, baseline_value=200.0, tolerance_pct=0.3,
                            drift_pct=0.5, passed=False, severity=severity, message="drifted")

    def test_critical_drift_maps_to_red(self):
        self.assertEqual(_finding_from_drift_result(self._drift("CRITICAL")).severity, RED_SEVERITY)

    def test_warning_drift_maps_to_yellow(self):
        self.assertEqual(_finding_from_drift_result(self._drift("WARNING")).severity, YELLOW_SEVERITY)

    def test_ok_drift_produces_no_finding(self):
        self.assertIsNone(_finding_from_drift_result(self._drift("OK")))


class UniverseDynamismCheckTest(SimpleTestCase):
    # claude code changed: new — regression-checks Milestone A item 3
    # (the fix for 3 modules that used to fall back to a small
    # hardcoded symbol list) against the ACTUAL current codebase.

    def test_all_three_fixed_modules_report_green(self):
        findings = check_universe_dynamism()
        self.assertEqual(len(findings), 3)
        for f in findings:
            self.assertEqual(f.severity, GREEN_SEVERITY, f"{f.component} did not report GREEN: {f.evidence}")

    def test_detects_a_reverted_hardcoded_fallback(self):
        """Proves this check can actually CATCH the regression it
        claims to catch, not just that it currently passes — patches
        inspect.getsource to simulate a module whose source no longer
        calls symbols_for_asset_class()."""
        with patch("bot.management.commands.deep_health_check.inspect.getsource", return_value="SYMBOLS = ['BTCUSDT']"):
            findings = check_universe_dynamism()
        self.assertTrue(all(f.severity == RED_SEVERITY for f in findings))


class GovernanceBoundaryCheckTest(SimpleTestCase):

    def test_lists_all_six_named_engines(self):
        findings = check_governance_boundary()
        standing = [f for f in findings if f.remediation_status == "known gap, accepted — deferred to a dedicated follow-up mission"]
        self.assertEqual(len(standing), 1)
        for rel_path in UNGOVERNED_RESEARCH_ENGINES:
            self.assertIn(rel_path, standing[0].evidence)
        self.assertEqual(standing[0].severity, YELLOW_SEVERITY)

    def test_flags_a_missing_engine_file_as_orange(self):
        with patch("bot.management.commands.deep_health_check.UNGOVERNED_RESEARCH_ENGINES",
                   ["bot/research/this_file_does_not_exist.py"]):
            findings = check_governance_boundary()
        orange = [f for f in findings if f.severity == ORANGE_SEVERITY]
        self.assertEqual(len(orange), 1)
        self.assertIn("this_file_does_not_exist.py", orange[0].evidence)


class DatasetFingerprintCoverageCheckTest(SimpleTestCase):

    def test_run_cross_sectional_oos_is_green(self):
        findings = check_dataset_fingerprint_coverage()
        by_component = {f.component: f for f in findings}
        self.assertEqual(len(findings), len(FINGERPRINT_COVERAGE_ENGINES))
        self.assertEqual(by_component["bot/research/run_cross_sectional_oos.py"].severity, GREEN_SEVERITY)

    def test_other_nine_engines_are_yellow(self):
        findings = check_dataset_fingerprint_coverage()
        by_component = {f.component: f for f in findings}
        for rel_path in FINGERPRINT_COVERAGE_ENGINES:
            if rel_path == "bot/research/run_cross_sectional_oos.py":
                continue
            self.assertEqual(by_component[rel_path].severity, YELLOW_SEVERITY, rel_path)


class ReconciliationGateCheckTest(SimpleTestCase):
    # claude code changed: new — regression-checks Milestone A item 2
    # using PositionTracker's own real reconcile_with_exchange(), not a
    # mock of it, so this genuinely proves the gate still works.

    def test_current_implementation_reports_green(self):
        findings = check_reconciliation_gate()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, GREEN_SEVERITY)

    def test_detects_a_reverted_always_true_reconciliation(self):
        """Proves this check can actually CATCH the exact regression it
        exists to guard against — patches PositionTracker.is_reconciled
        to always read True after reconcile_with_exchange runs, the
        precise bug Milestone A item 2 fixed."""
        from bot.engines.position_tracker import PositionTracker
        original_method = PositionTracker.reconcile_with_exchange

        def _always_reconciled(self, exchange, symbols):
            original_method(self, exchange, symbols)
            self.is_reconciled = True   # simulate the old, broken behavior

        with patch.object(PositionTracker, "reconcile_with_exchange", _always_reconciled):
            findings = check_reconciliation_gate()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, RED_SEVERITY)
        self.assertIn("REGRESSED", findings[0].remediation_status)


class ForexDatasetHealthChecksTest(SimpleTestCase):
    # claude code changed: new — Forex Integration Forensic Verification,
    # Phase 12. Real checks against the real 8 registered Forex CSVs
    # (data/forex/*.csv) already on disk from the prior mission — no
    # mocking, matching this project's own testing convention, since
    # these checks exist specifically to catch real data-quality/
    # freshness regressions, which a mocked file could never do.

    def test_freshness_check_returns_one_finding_per_registered_symbol(self):
        from bot.instruments import ASSET_CLASS_FOREX, symbols_for_asset_class
        findings = check_forex_dataset_freshness()
        self.assertEqual(len(findings), len(symbols_for_asset_class(ASSET_CLASS_FOREX)))
        for f in findings:
            self.assertIn(f.severity, (GREEN_SEVERITY, YELLOW_SEVERITY, RED_SEVERITY))

    def test_quality_check_reports_zero_violations_on_real_data(self):
        """Real data, real assertion — the prior forensic audit (both an
        independent fork and this file's own author) confirmed 0 OHLC
        violations and 0 duplicate timestamps across all 8 real Forex
        CSVs. If this ever regresses (corrupted refetch, a provider
        change), this test must fail, not silently pass."""
        findings = check_forex_dataset_quality()
        self.assertGreater(len(findings), 0)
        for f in findings:
            self.assertEqual(f.severity, GREEN_SEVERITY, f.evidence)

    def test_capability_governance_check_is_all_green_today(self):
        """Regression guard for the exact bug this mission's Phase 10
        found and fixed (asset_class gating existed but wasn't wired into
        the real orchestrator/formalize call sites) — proves the check
        itself currently reports both directions correctly."""
        findings = check_forex_capability_governance()
        self.assertEqual(len(findings), 2)
        for f in findings:
            self.assertEqual(f.severity, GREEN_SEVERITY, f.evidence)

    def test_capability_governance_check_catches_a_reverted_leak(self):
        """Proves this check can actually CATCH the regression it claims
        to catch — not just observe the current passing state. Monkeypatches
        a capability's supported_asset_classes to (incorrectly) include
        FOREX and confirms the 'blocked' finding correctly flips to RED."""
        from bot.research_lab.capability_registry import RESEARCH_CAPABILITIES
        classes_list = RESEARCH_CAPABILITIES["cross_sectional_research"].supported_asset_classes
        original = list(classes_list)  # ResearchCapability is frozen, but the list it holds is mutable — mutate in place, restore after
        classes_list.append("FOREX")
        try:
            findings = check_forex_capability_governance()
        finally:
            classes_list[:] = original
        by_check = {f.check: f for f in findings}
        blocked_finding = by_check["a Forex-unsupported capability is blocked for FOREX, not silently allowed"]
        self.assertEqual(blocked_finding.severity, RED_SEVERITY)
        self.assertIn("REGRESSED", blocked_finding.remediation_status)

    def test_fingerprint_reproducibility_check_is_green_on_real_data(self):
        findings = check_forex_dataset_fingerprint_reproducibility()
        self.assertGreater(len(findings), 0)
        for f in findings:
            self.assertEqual(f.severity, GREEN_SEVERITY, f.evidence)


class ForexCrossSectionalResearchWiringHealthCheckTest(SimpleTestCase):
    # claude code changed: new — Forex Integration Stage 3 groundwork.
    # A real structural regression guard for the exact bug class Stage 1
    # found (crypto-only entry points silently never reaching Forex) and
    # Stage 3 groundwork fixed in run_cross_sectional_oos.py.

    def test_all_three_wiring_checks_are_green_on_the_current_code(self):
        findings = check_forex_cross_sectional_research_wiring()
        self.assertEqual(len(findings), 3)
        for f in findings:
            self.assertEqual(f.severity, GREEN_SEVERITY, f"{f.check}: {f.evidence}")

    def test_catches_a_reverted_asset_class_parameter(self):
        """Proves this check can actually CATCH the regression it claims
        to catch, not just observe the current passing state."""
        import inspect
        from unittest.mock import patch

        def fake_signature_without_asset_class(fn):
            real_sig = inspect.Signature.from_callable(fn)
            params = [p for name, p in real_sig.parameters.items() if name != "asset_class"]
            return real_sig.replace(parameters=params)

        with patch("inspect.signature", side_effect=fake_signature_without_asset_class):
            findings = check_forex_cross_sectional_research_wiring()
        by_check = {f.check: f for f in findings}
        param_finding = by_check["run_cross_sectional_research() accepts an asset_class parameter"]
        self.assertEqual(param_finding.severity, RED_SEVERITY)
