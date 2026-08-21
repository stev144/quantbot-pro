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
    # claude code changed: new entry — Phase A of the controlled remediation
    # program (forensic-audit finding P0-1). Before this entry existed,
    # MovingAverageStrategy had no row here at all, and StrategyRouter never
    # asked this registry about it either (it only gated the RANGING
    # branch) — so the strategy that actually produces every live/backtest
    # trade in this system had silently never been asked to earn its
    # eligibility. This entry makes the true state explicit rather than
    # leaving it to the (also-correct, but silent) get_strategy_verdict()
    # fail-closed fallback: MovingAverageStrategy's EMA/structure/pullback
    # entry logic (bot/strategies/moving_average.py) has never been run
    # through feature_validator.py, permutation_test_engine.py, or
    # walk_forward_engine.py — no IC test, no significance test, nothing.
    # It is UNTESTED, not REJECTED — the evidence to reject or support it
    # simply doesn't exist yet. production_eligible stays False until it
    # does, per Rule 4 (fail closed: unknown != validated).
    "MovingAverageStrategy": StrategyVerdict(
        strategy_name="MovingAverageStrategy",
        research_verdict=UNTESTED,
        production_eligible=False,
        rejection_reason=(
            "No research evidence has ever been recorded for this strategy's "
            "EMA-trend + structure-confirmation + pullback entry logic "
            "(bot/strategies/moving_average.py). It has not been run through "
            "feature_validator.py's significance testing, "
            "permutation_test_engine.py, or walk_forward_engine.py — those "
            "pipelines have only ever been run against the AVAX/ATOM and "
            "DOT/LINK Kalman pairs (both REJECTED) and, separately, against "
            "MeanReversionStrategy's rsi/bb_width features (also REJECTED). "
            "MovingAverageStrategy's own EMA/structure/pullback thresholds "
            "(bot/strategies/moving_average.py's ema_fast_period, "
            "ema_slow_period, pullback_tolerance, sl_range_factor, "
            "tp_rr_ratio, and StructureAnalyser's lookback/min_swing_points/"
            "min_structure_change) have no cited backtest or statistical "
            "evidence anywhere in this repository. Until a real validation "
            "run produces IC/significance/robustness evidence for this "
            "strategy specifically, it remains UNTESTED, not SUPPORTED — "
            "see research_data/model_governance_log.md for the standard "
            "this project holds every other strategy candidate to."
        ),
    ),
    "MeanReversionStrategy": StrategyVerdict(
        strategy_name="MeanReversionStrategy",
        research_verdict=REJECTED,
        production_eligible=False,
        # claude code changed: Phase A of the controlled remediation program
        # (forensic-audit finding P0-2). The previous version of this
        # citation described a "re-run" whose numbers could not actually be
        # reproduced from the repository's state — the research_data/
        # *_validated_features.csv files it pointed to predated the
        # feature_validator.py multiple-testing-correction bugfix (commit
        # a4113f4) by three weeks and still carried that bug's exact
        # signature. Rather than editing the prose, this entry now cites a
        # real, from-scratch, reproducible re-run performed 2026-08-20:
        # bot/fetch_all_symbols.py re-fetched all 20 symbols fresh from
        # Binance, then `python -m bot.run_research_all` re-ran
        # feature_validator.py's institutional validation (today's code,
        # not a prior session's) against that fresh data, regenerating
        # every research_data/*_validated_features.csv this citation
        # references. The REJECTED verdict reproduces: see rejection_reason
        # below for the numbers this run actually produced. Two figures
        # shifted slightly from the prior (unreproducible) citation — the
        # bb_width FDR-pass count (was cited as 4/20, this run measured
        # 3/20) — expected, since this run's 50k-candle window ends
        # 2026-08-20, not whatever window backed the original prose. The
        # decision-relevant conclusions (REVIEW/DELETE/KEEP counts,
        # economic-significance verdict) reproduced exactly.
        rejection_reason=(
            "rsi: statistically real (20/20 symbols pass real FDR-corrected "
            "significance, p_fdr as low as 2.9e-27) but economically "
            "negligible everywhere — quartile spread 0.0000129-0.00122 vs. "
            "the 0.5% economic-significance threshold, |IC| 0.021-0.061, "
            "econ_significant=False on 20/20 symbols — REVIEW on 20/20 "
            "symbols, KEEP on 0/20. bb_width: 3/20 symbols pass FDR "
            "correction (FIL, MATIC, SHIB); where significant, IC is still "
            "negligible — DELETE on 18/20 symbols, REVIEW on 2/20 (MATIC, "
            "SHIB), KEEP on 0/20. Neither feature has been through "
            "permutation_test_engine.py or walk_forward_engine.py — that "
            "pipeline has only ever run on the AVAX/ATOM and DOT/LINK "
            "Kalman pairs, both REJECTED. Reproduced 2026-08-20 from "
            "research_data/*_validated_features.csv (all 20 regenerated "
            "same run) and research_data/model_governance_log.md."
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
