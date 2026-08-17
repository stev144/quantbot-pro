# claude code changed: new file — Phase 1 of the research-driven strategy
# architecture refactor. Gives StrategyRouter a single place to ask "is this
# strategy allowed to trade live/in backtest" instead of dispatching to any
# strategy that exists in code. Deliberately minimal: no CSV-parsing, no
# per-feature data model — just a small, honest, hardcoded verdict lookup.
# A later phase can swap the hardcoded STRATEGY_VERDICTS dict for something
# that reads live research_data/*.csv output without changing the call site
# in strategy_router.py (get_strategy_verdict()'s signature stays the same).
#
# Verdict taxonomy matches what's already used elsewhere in this project's
# research docs/UI (research_data/model_governance_log.md, parsed by
# bot/views/research_archive.py): SUPPORTED / CONDITIONAL / WEAK / REJECTED
# / UNTESTED. Only SUPPORTED strategies are production_eligible.

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Verdict states ───────────────────────────────────────────────────────
SUPPORTED   = "SUPPORTED"
CONDITIONAL = "CONDITIONAL"
WEAK        = "WEAK"
REJECTED    = "REJECTED"
UNTESTED    = "UNTESTED"

VALID_VERDICTS = {SUPPORTED, CONDITIONAL, WEAK, REJECTED, UNTESTED}


@dataclass(frozen=True)
class StrategyVerdict:
    strategy_name:      str
    research_verdict:   str
    production_eligible: bool
    rejection_reason:   str


# ── Hardcoded verdicts ───────────────────────────────────────────────────
# Each entry cites the specific evidence it's based on. Checked directly
# against research_data/*_validated_features.csv and
# research_data/model_governance_log.md before writing this, not assumed.
STRATEGY_VERDICTS: dict[str, StrategyVerdict] = {
    "MeanReversionStrategy": StrategyVerdict(
        strategy_name="MeanReversionStrategy",
        research_verdict=REJECTED,
        production_eligible=False,
        # claude code changed: re-run against forward_return_4h across all
        # 20 tracked symbols after fixing two real bugs in
        # feature_validator.py's multiple-testing correction (it crashed
        # unpacking multipletests()'s return, then fell into an except
        # block that hardcoded passes_fdr/passes_bonferroni=False for
        # EVERY feature regardless of the true result — so the previous
        # "0/20 pass FDR/Bonferroni" here was an artifact of that bug, not
        # a real finding). The REJECTED verdict itself is unchanged, but
        # the evidence backing it was wrong and is corrected below.
        rejection_reason=(
            "rsi: statistically real (20/20 symbols pass real FDR-corrected "
            "significance, p_fdr as low as 1e-27) but economically "
            "negligible everywhere — quartile spread 0.00001-0.0012 vs. "
            "the 0.5% economic-significance threshold, |IC| 0.02-0.06 "
            "(mostly below the 0.05 'acceptable' bar) — REVIEW on 20/20 "
            "symbols, KEEP on 0/20. bb_width: only 4/20 symbols even pass "
            "FDR correction; where significant, IC is still negligible — "
            "DELETE on 18/20 symbols, REVIEW on 2/20 (MATIC, SHIB), KEEP "
            "on 0/20. Neither feature has been through "
            "permutation_test_engine.py or walk_forward_engine.py — that "
            "pipeline has only ever run on the AVAX/ATOM and DOT/LINK "
            "Kalman pairs, both REJECTED. See research_data/model_governance_log.md."
        ),
    ),
}


def get_strategy_verdict(strategy_name: str) -> StrategyVerdict:
    """
    Look up whether a strategy is allowed to fire production trades.

    Fails closed: a strategy name not present in STRATEGY_VERDICTS returns
    UNTESTED / production_eligible=False rather than silently allowing it.
    A strategy only becomes eligible by an explicit, evidenced entry above
    — never by omission.
    """
    verdict = STRATEGY_VERDICTS.get(strategy_name)
    if verdict is None:
        return StrategyVerdict(
            strategy_name=strategy_name,
            research_verdict=UNTESTED,
            production_eligible=False,
            rejection_reason=(
                f"'{strategy_name}' has no entry in STRATEGY_VERDICTS — "
                f"no research evidence has been recorded for it, so it "
                f"defaults to not production-eligible."
            ),
        )
    return verdict
