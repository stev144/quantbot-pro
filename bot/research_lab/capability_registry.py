# ============================================================
# bot/research_lab/capability_registry.py
# Research Lab — the Research Capability Registry (Advanced Quant Research
# Capability Architecture & Subscription Entitlements, section 3).
#
# claude code changed: new file. The single authoritative list of every
# research capability the Research Lab could ever expose — CORE (available
# to any authenticated user) and PRO (requires an active subscription).
#
# Two hard rules this file exists to enforce structurally, not just by
# convention:
#
#   1. "A research engine existing in the repository does NOT automatically
#      mean it is ready for customers" (section 4). engine_status is set
#      from a real, personally-verified read of each backing module — not
#      "the file exists, therefore READY". Only engine_status ==
#      IMPLEMENTED_AND_READY may ever be dispatched by the orchestrator;
#      every other status is a real, currently-true statement about a real
#      gap (missing tests, missing multiple-testing correction, an
#      untested shared dependency, no Research Lab tool wrapper yet, or no
#      orchestrator dispatch branch yet even though the tool is wired).
#
#   2. Subscription tier and engine readiness are DELIBERATELY two
#      independent axes (section 10) — this file only defines the static
#      per-capability facts (tier required, engine status). Whether a
#      SPECIFIC user can run a SPECIFIC capability right now is computed
#      by bot/research_lab/entitlements.py, never stored here — a
#      capability's availability depends on who's asking, this registry
#      does not.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

CORE = "CORE"
PRO = "PRO"
SUBSCRIPTION_TIERS = [CORE, PRO]

# claude code changed: new — six-state engine readiness taxonomy, per
# section 4. Deliberately richer than a bool: "the module exists" and
# "this is safe to sell" are different questions, and the taxonomy exists
# to make every step between them a recorded, auditable fact rather than
# an implicit assumption.
IMPLEMENTED_AND_READY = "IMPLEMENTED_AND_READY"
IMPLEMENTED_BUT_NEEDS_TESTS = "IMPLEMENTED_BUT_NEEDS_TESTS"
IMPLEMENTED_BUT_NEEDS_AUDIT = "IMPLEMENTED_BUT_NEEDS_AUDIT"
PARTIALLY_IMPLEMENTED = "PARTIALLY_IMPLEMENTED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
BLOCKED_BY_DEPENDENCY = "BLOCKED_BY_DEPENDENCY"

ENGINE_STATUS_CHOICES = [
    IMPLEMENTED_AND_READY, IMPLEMENTED_BUT_NEEDS_TESTS, IMPLEMENTED_BUT_NEEDS_AUDIT,
    PARTIALLY_IMPLEMENTED, NOT_IMPLEMENTED, BLOCKED_BY_DEPENDENCY,
]

# claude code changed: new — research category, per section 8's suggested
# grouping. Purely a UI/catalog organization concern, not enforced by any
# gate.
SIGNAL_RESEARCH = "Signal Research"
RELATIONSHIP_RESEARCH = "Relationship Research"
MARKET_STRUCTURE_RESEARCH = "Market Structure Research"
VALIDATION = "Validation"


@dataclass(frozen=True)
class ResearchCapability:
    """
    One immutable, static definition. Nothing here is user-specific —
    see entitlements.py for the per-user access decision.
    """

    id: str
    name: str
    description: str
    category: str
    required_tier: str          # CORE | PRO — section 3 "required subscription entitlement"
    risk_tier: str               # LOW | MEDIUM | HIGH — matches spec.py's RISK_TIERS / policy_gate.py's TOOLS_BY_RISK_TIER, the SAME risk concept an experiment itself declares
    compute_tier: str            # LOW | MEDIUM | HIGH — informational proxy for expected wall-clock/compute cost, independent of risk_tier (a LOW-risk tool can still be compute-heavy)
    backing_engine: str          # dotted path — informational, points at the real module this wraps, never a second implementation
    engine_status: str           # one of ENGINE_STATUS_CHOICES — section 4
    has_tests: bool              # section 3 "whether it is currently tested"
    hypothesis_type: Optional[str] = None   # the ResearchSpec.hypothesis_type this capability maps to, if any (None = not yet reachable via any spec shape)
    ui_visible: bool = True      # section 10/11 — capabilities are visible-but-locked by design; False is reserved for something not yet announced at all
    status_note: str = ""        # short, honest, user-facing reason for the current engine_status — shown verbatim in the "temporarily unavailable" UI state
    compute_budget: Dict = field(default_factory=dict)   # section 13 — capability-specific limits, enforced by policy_gate.py, never removed by subscription tier
    # claude code changed: new — Multi-Asset Foundation Refactor STEP 9.
    # Which asset classes this SPECIFIC capability actually works for —
    # deliberately per-capability, not a platform-wide flag, since a
    # future capability could plausibly reach CRYPTO+US_EQUITY before
    # FOREX (or never reach one at all) independently of any other
    # capability's own readiness. Defaults to CRYPTO-only for every entry
    # below because that is, honestly, the only asset class any backing
    # engine in this registry has ever been run against — not a forecast
    # of future readiness, a statement of current fact. Not yet consulted
    # by entitlements.py/can_access() this phase (section 14 of the
    # refactor brief: prepare the boundary, don't wire enforcement that
    # has nothing real to enforce yet — every capability supports exactly
    # the same one asset class today, so gating on it would be a no-op).
    supported_asset_classes: List[str] = field(default_factory=lambda: ["CRYPTO"])

    @property
    def operationally_ready(self) -> bool:
        """
        claude code changed: new — the ONLY thing that may ever gate
        orchestrator dispatch. Computed, not stored twice, so it can never
        drift from engine_status by a forgotten second edit.
        """
        return self.engine_status == IMPLEMENTED_AND_READY


# claude code changed: new — the registry itself. Every capability the
# Phase A audit found or could find, whether or not it is dispatchable
# today — section 4's whole point is that "in the registry" and "safe to
# run" are different claims, so absent-from-here would be a worse
# information loss than present-but-NOT_IMPLEMENTED.
RESEARCH_CAPABILITIES: Dict[str, ResearchCapability] = {
    c.id: c for c in [

        ResearchCapability(
            id="continuous_feature_research",
            name="Continuous Feature Research",
            description="Test whether a feature's general level correlates with forward returns (Spearman IC, block-permutation significance, FDR correction).",
            category=SIGNAL_RESEARCH, required_tier=CORE, risk_tier="LOW", compute_tier="LOW",
            backing_engine="bot.research.feature_validator.FeatureValidator",
            engine_status=IMPLEMENTED_AND_READY, has_tests=True, hypothesis_type="feature",
        ),
        ResearchCapability(
            id="conditional_event_research",
            name="Conditional Event Research",
            description='Test event hypotheses ("WHEN feature crosses a threshold, THEN...") via Mann-Whitney U and block-permutation on the condition-true vs. condition-false subsets.',
            category=SIGNAL_RESEARCH, required_tier=CORE, risk_tier="LOW", compute_tier="LOW",
            backing_engine="bot.research_lab.tools.conditional_tools.run_conditional_test",
            engine_status=IMPLEMENTED_AND_READY, has_tests=True, hypothesis_type="conditional",
        ),
        ResearchCapability(
            id="feature_stability_research",
            name="Feature Stability Research",
            description="Test whether a feature's predictive power is stable across time periods and market regimes, not just significant on average.",
            category=SIGNAL_RESEARCH, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.research.feature_stability_analyzer",
            engine_status=NOT_IMPLEMENTED, has_tests=True,
            status_note="No Research Lab tool wrapper exists yet — the underlying engine has 5 tests but has never been wired into the Tool Layer.",
        ),
        ResearchCapability(
            id="feature_decay_research",
            name="Feature Decay Research",
            description="Test whether a feature's edge is decaying over time (a real signal that has been arbitraged away vs. one that remains stable).",
            category=SIGNAL_RESEARCH, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.research.feature_decay_analyzer",
            engine_status=NOT_IMPLEMENTED, has_tests=True,
            status_note="No Research Lab tool wrapper exists yet — its IC thresholds are already reused by verdict.py, but the decay analysis itself is not exposed as a capability.",
        ),
        ResearchCapability(
            id="cointegration_pairs_research",
            name="Cointegration & Pairs Research",
            description="Discover whether two assets' spread is statistically cointegrated and exhibits stable mean reversion — Engle-Granger two-step, ADF test, OLS hedge ratio, half-life.",
            category=RELATIONSHIP_RESEARCH, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.research.cointegration_engine.CointegrationEngine",
            engine_status=IMPLEMENTED_AND_READY, has_tests=True, hypothesis_type="pairs",
            compute_budget={"max_pairs_per_experiment": 1},
        ),
        ResearchCapability(
            id="kalman_dynamic_hedge_ratio",
            name="Dynamic Hedge-Ratio (Kalman) Research",
            description="Track a pair's hedge ratio dynamically over time via a Kalman filter instead of a single fixed OLS estimate, and test whether the dynamic spread improves signal quality.",
            category=RELATIONSHIP_RESEARCH, required_tier=PRO, risk_tier="HIGH", compute_tier="HIGH",
            backing_engine="bot.research.kalman_filter_engine.KalmanFilterEngine",
            # claude code changed: PARTIALLY_IMPLEMENTED -> IMPLEMENTED_BUT_NEEDS_AUDIT —
            # Phase 1D (Kalman Research Integration). Evidence for the change: a
            # typed, arbitrary-pair tool-call now exists and is tested
            # (bot.research_lab.tools.research_tools.run_kalman_pairs_test,
            # bot/tests/test_kalman_research_lab_integration.py, 21 tests) — the
            # specific gap the prior status_note named ("not the typed, arbitrary-
            # pair tool-call shape every other capability uses") is resolved. NOT
            # promoted to IMPLEMENTED_AND_READY: see status_note below for the two
            # real reasons it still needs audit before real exposure.
            engine_status=IMPLEMENTED_BUT_NEEDS_AUDIT, has_tests=True,
            status_note=(
                "The filter math is real and tested — including a verified, regression-tested fix for a "
                "posterior-state leakage bug in the tradeable spread, and (Phase 1D) confirmed leakage-free "
                "z-score normalisation (no center=True rolling windows). A typed, arbitrary-pair Research Lab "
                "tool now exists (run_kalman_pairs_test) and reuses the fresh-OLS-seed pattern "
                "run_cointegration_test already established — never a duplicate cointegration implementation. "
                "Two real reasons this still needs audit, not exposure: (1) it is NOT in policy_gate.py's "
                "ALLOWED_TOOLS/TOOLS_BY_RISK_TIER — risk_tier=HIGH is categorically unreachable by the Research "
                "Lab MVP's own design (is_tool_allowed() rejects any risk_tier other than LOW/MEDIUM), so this "
                "tool is directly callable/testable but not reachable through a real Research Lab request; "
                "promoting it would require a deliberate decision about whether Kalman's risk_tier itself should "
                "change, which this phase left alone. (2) OBSERVATION_NOISE's default (1e-3) was empirically "
                "calibrated to AVAX/ATOM's own historical price variance and is never re-estimated per pair — "
                "already configurable via a constructor override, but no data-driven estimation path exists yet "
                "(Phase 1D evaluated several approaches and deliberately did not implement one pending its own "
                "look-ahead-bias validation — see the Phase 1D engineering report, Objectives 2-3)."
            ),
        ),
        ResearchCapability(
            id="cross_sectional_research",
            name="Cross-Sectional Relationship Research",
            description="Test relative-rank and cross-sectional z-score features computed across the whole symbol universe at each timestamp, not just one asset in isolation.",
            category=RELATIONSHIP_RESEARCH, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.research.cross_section_engine",
            engine_status=NOT_IMPLEMENTED, has_tests=True,
            status_note="No Research Lab tool wrapper exists yet — the underlying engine has thin test coverage (3 tests) relative to its neighbors and would need further audit before exposure.",
        ),
        ResearchCapability(
            id="contagion_divergence_research",
            name="Cross-Asset Divergence Research",
            description="Test whether a specific altcoin's short-term divergence from BTC predicts its own mean reversion in the following hours (a one-directional BTC-to-altcoin divergence test, not a full multi-asset contagion network).",
            category=MARKET_STRUCTURE_RESEARCH, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.research.contagion_engine.ContagionEngine",
            engine_status=IMPLEMENTED_BUT_NEEDS_AUDIT, has_tests=True,
            status_note=(
                "The feature computation has a verified, regression-tested fix for a look-ahead bias in its "
                "winsorization step. However its significance reporter (DivergenceICReporter) flags results at a "
                "raw p<0.05 threshold with no multiple-testing correction, despite naturally producing many "
                "p-values per altcoin x horizon combination — the same family-wide FDR discipline every other "
                "significance-testing capability in this registry already has. Withheld pending that fix."
            ),
        ),
        ResearchCapability(
            id="walk_forward_validation",
            name="Walk-Forward Validation",
            description="Anchored walk-forward out-of-sample validation — trains on an expanding window, tests on each subsequent fold, reports combined out-of-sample performance.",
            category=VALIDATION, required_tier=PRO, risk_tier="HIGH", compute_tier="HIGH",
            backing_engine="bot.research.walk_forward_engine.WalkForwardEngine",
            engine_status=BLOCKED_BY_DEPENDENCY, has_tests=True,
            # claude code changed: status_note updated — Phase 1C (Blocker A)
            # added bot/tests/test_entry_exit_engine.py (16 tests: pair
            # identity across asset classes, cost-model integration,
            # position-sizing/entry/exit math, security boundary), so the
            # "zero automated test coverage" reason no longer holds. Still
            # BLOCKED_BY_DEPENDENCY: it remains excluded from the Research
            # Lab's tool allowlist, a separate wiring gap this phase
            # deliberately did not touch (Phase 1C's own scope explicitly
            # excludes exposing advanced capabilities). Re-evaluate
            # engine_status once that wiring work happens.
            status_note=(
                "entry_exit_engine.py now has real test coverage (bot/tests/test_entry_exit_engine.py, "
                "16 tests, added Phase 1C) — the prior 'zero automated test coverage' blocker is resolved. "
                "Still excluded from the Research Lab's tool allowlist pending dedicated integration wiring, "
                "which is a separate, not-yet-scheduled step."
            ),
        ),
        ResearchCapability(
            id="permutation_robustness_testing",
            name="Block-Permutation Robustness Testing",
            description="Full-pipeline block-permutation significance testing for a trading rule's entry/exit logic (distinct from the block-permutation already used for feature and conditional-event significance).",
            category=VALIDATION, required_tier=PRO, risk_tier="HIGH", compute_tier="HIGH",
            backing_engine="bot.research.permutation_test_engine",
            engine_status=BLOCKED_BY_DEPENDENCY, has_tests=True,
            # claude code changed: status_note updated — same reason as
            # walk_forward_validation above; permutation_test_engine.py's
            # full run() path also depends on entry_exit_engine.py.
            status_note=(
                "entry_exit_engine.py now has real test coverage (bot/tests/test_entry_exit_engine.py, "
                "16 tests, added Phase 1C) — the prior 'zero automated test coverage' blocker is resolved. "
                "Still excluded from the Research Lab's tool allowlist pending dedicated integration wiring, "
                "which is a separate, not-yet-scheduled step."
            ),
        ),
        ResearchCapability(
            id="parameter_sensitivity_analysis",
            name="Parameter Sensitivity Analysis",
            description="Test how sensitive a strategy's performance is to small parameter changes — a strategy that only works for one exact parameter set is a fragile one.",
            category=VALIDATION, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.engines.strategy_scorer.StrategyScorer",
            engine_status=IMPLEMENTED_BUT_NEEDS_TESTS, has_tests=True,
            status_note="The Research Lab tool wrapper (run_parameter_sensitivity) exists and is tool-tested, but StrategyScorer itself has only 2 tests platform-wide and no orchestrator dispatch branch yet.",
        ),
        ResearchCapability(
            id="backtest_validation",
            name="Backtest Validation",
            description="Run a full regime-aware backtest for a hypothesis and report trade-level performance (win rate, expectancy, Sharpe, drawdown) alongside the statistical evidence.",
            category=VALIDATION, required_tier=PRO, risk_tier="MEDIUM", compute_tier="MEDIUM",
            backing_engine="bot.backtesting.backtester.Backtester",
            engine_status=PARTIALLY_IMPLEMENTED, has_tests=True,
            status_note="The Research Lab tool wrapper (run_backtest) exists and is tool-tested, and the backtester itself is the most heavily used engine in the whole platform — but there is no orchestrator dispatch branch wiring it into an experiment yet.",
        ),
    ]
}


def get_capability(capability_id: str) -> Optional[ResearchCapability]:
    return RESEARCH_CAPABILITIES.get(capability_id)


def capability_for_hypothesis_type(hypothesis_type: str) -> Optional[ResearchCapability]:
    """
    claude code changed: new — the single mapping from a ResearchSpec's
    hypothesis_type to the capability that governs it. A hypothesis_type
    with no matching capability (should never happen for a value in
    spec.py's HYPOTHESIS_TYPES) returns None, which callers must treat as
    fail-closed, not "no capability required".
    """
    for capability in RESEARCH_CAPABILITIES.values():
        if capability.hypothesis_type == hypothesis_type:
            return capability
    return None


def list_by_category() -> Dict[str, List[ResearchCapability]]:
    """claude code changed: new — catalog view grouping, in registry
    insertion order within each category (not sorted, so ordering is an
    explicit editorial choice made in RESEARCH_CAPABILITIES above)."""
    grouped: Dict[str, List[ResearchCapability]] = {}
    for capability in RESEARCH_CAPABILITIES.values():
        grouped.setdefault(capability.category, []).append(capability)
    return grouped
