# ============================================================
# bot/research_lab/policy_gate.py
# Research Lab — the Research Policy Gate (section 10).
#
# claude code changed: new file. Fail-closed: an experiment must clear
# every check here before a single tool call is allowed to run. This gate
# is what stands between a validated ResearchSpec and the Tool Layer
# (bot/research_lab/tools/) — nothing in the Tool Layer is ever invoked
# without a PolicyDecision.allowed == True from this module first.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from bot.research_lab.data_availability import DataAvailabilityReport
from bot.research_lab.spec import ResearchSpec

# claude code changed: new — the fixed, closed allowlist of tools the
# Research Lab MVP may ever call, matching bot/research_lab/tools/ (task
# 32). A tool name outside this set is rejected regardless of caller —
# there is no path for the AI (or a bug) to request an unlisted tool.
# kalman_filter_engine/walk_forward_engine/permutation_test_engine and
# anything depending on entry_exit_engine.py are deliberately EXCLUDED —
# per the implementation assessment, entry_exit_engine.py (their shared
# dependency) has zero test coverage.
ALLOWED_TOOLS = {
    "inspect_dataset",
    "calculate_feature",
    "run_statistical_test",
    "run_fdr_correction",
    "run_conditional_test",  # claude code changed: new — Conditional Hypothesis Integrity fix, real event/conditional test, see tools/conditional_tools.py
    "run_cointegration_test",
    "run_backtest",
    "run_parameter_sensitivity",
    "generate_research_report",
}

# claude code changed: new — explicit, configurable compute/scope limits
# per section 10 and section 23. All plain module constants (not Django
# settings) for this MVP pass — easy to promote to settings.py later if an
# operator needs to tune them without a code change, but not overbuilt now.
MAX_SYMBOLS_PER_EXPERIMENT = 1  # claude code changed: ResearchSpec.asset is single-symbol by design this phase
MAX_TARGET_HORIZON_CANDLES = 24 * 30  # 30 days of 1h candles
MAX_FEATURES_PER_EXPERIMENT = 10
MAX_CONCURRENT_EXPERIMENTS_PER_USER = 3

# claude code changed: new — risk_tier gates which tools are reachable at
# all. MEDIUM tools (backtest/cointegration/sensitivity — real compute
# cost) require explicit approval before RUNNING even once PLANNED; LOW
# tools do not. There is no tier above MEDIUM reachable by any tool this
# module allows — HIGH is reserved by the spec schema for hypotheses this
# gate will never approve computing at all (matches section 3: no path to
# live execution regardless of declared risk_tier).
TOOLS_BY_RISK_TIER = {
    "LOW": {"inspect_dataset", "calculate_feature", "run_statistical_test", "run_fdr_correction", "run_conditional_test", "generate_research_report"},
    "MEDIUM": {"run_cointegration_test", "run_backtest", "run_parameter_sensitivity"},
}


@dataclass
class PolicyDecision:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    requires_approval: bool = False
    budget: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "allowed_tools": list(self.allowed_tools),
            "requires_approval": self.requires_approval,
            "budget": dict(self.budget),
        }


def is_tool_allowed(tool_name: str, risk_tier: str) -> bool:
    """Whether a single named tool is reachable at all for a given
    risk_tier — the check every individual tool call re-verifies at
    execution time (§08's "parameter validation... explicit name"), not
    just once at plan time."""
    if tool_name not in ALLOWED_TOOLS:
        return False
    if risk_tier not in ("LOW", "MEDIUM"):
        return False  # claude code changed: fixed real bug — HIGH (and any other value) must reach no tool at all
    if tool_name in TOOLS_BY_RISK_TIER["LOW"]:
        return True  # claude code changed: LOW-tier tools are usable at both LOW and MEDIUM risk_tier — a MEDIUM request is a superset, not a different set
    if tool_name in TOOLS_BY_RISK_TIER["MEDIUM"]:
        return risk_tier == "MEDIUM"  # claude code changed: fixed real bug — this branch used to always return True whenever tier=="LOW" was checked first, regardless of the caller's actual risk_tier
    return False


def evaluate_policy(
    spec: ResearchSpec,
    availability: DataAvailabilityReport,
    active_experiment_count: int = 0,
) -> PolicyDecision:
    """
    The single fail-closed checkpoint before any tool call is permitted.
    Every reason a request is blocked is collected (not just the first),
    so the Plan screen (section 11) can explain everything at once.
    """
    reasons: List[str] = []

    if spec.risk_tier == "HIGH":
        reasons.append("risk_tier HIGH is not supported by the Research Lab MVP")

    if not availability.all_available:
        missing = [c.name for c in availability.checks if not c.available]
        reasons.append(f"required data not available: {', '.join(missing)}")

    if len(spec.features) > MAX_FEATURES_PER_EXPERIMENT:
        reasons.append(f"{len(spec.features)} features requested, exceeds the {MAX_FEATURES_PER_EXPERIMENT}-feature limit per experiment")

    horizon = spec.target.get("horizon")
    if isinstance(horizon, int) and horizon > MAX_TARGET_HORIZON_CANDLES:
        reasons.append(f"target.horizon={horizon} exceeds the {MAX_TARGET_HORIZON_CANDLES}-candle maximum")

    if active_experiment_count >= MAX_CONCURRENT_EXPERIMENTS_PER_USER:
        reasons.append(f"{active_experiment_count} experiments already active, at the {MAX_CONCURRENT_EXPERIMENTS_PER_USER}-experiment concurrency limit")

    allowed_tools = sorted(
        t for t in ALLOWED_TOOLS if is_tool_allowed(t, spec.risk_tier)
    ) if spec.risk_tier != "HIGH" else []

    requires_approval = spec.risk_tier == "MEDIUM"  # claude code changed: MEDIUM-tier tools carry real compute cost — one explicit approval at the Plan stage, section 11

    return PolicyDecision(
        allowed=len(reasons) == 0,
        reasons=reasons,
        allowed_tools=allowed_tools,
        requires_approval=requires_approval,
        budget={
            "max_symbols": MAX_SYMBOLS_PER_EXPERIMENT,
            "max_target_horizon_candles": MAX_TARGET_HORIZON_CANDLES,
            "max_features": MAX_FEATURES_PER_EXPERIMENT,
            "max_concurrent_experiments": MAX_CONCURRENT_EXPERIMENTS_PER_USER,
        },
    )
