# ============================================================
# bot/views/strategy_pipeline.py
# claude code changed: new file — Phase 3 (UI pipeline view). Shows the
# LIVE production-gating state of every strategy StrategyRouter can
# dispatch to, sourced directly from
# bot/research/validated_feature_registry.py's STRATEGY_VERDICTS. This is
# deliberately distinct from research_archive.py/rejected_research.py,
# which parse the abstract markdown research log
# (research_data/model_governance_log.md) — pairs/features research
# history, not live router-eligibility state. Mirrors research_archive.py's
# thinness: one dict of real data in, one template out, no derived logic.
# ============================================================

from django.shortcuts import render

from bot.research.validated_feature_registry import STRATEGY_VERDICTS


def strategy_pipeline(request):
    strategies = [
        {
            "strategy_name": v.strategy_name,
            "research_verdict": v.research_verdict,
            "production_eligible": v.production_eligible,
            "rejection_reason": v.rejection_reason,
        }
        for v in STRATEGY_VERDICTS.values()
    ]
    context = {"strategies": strategies}
    return render(request, "strategy_pipeline.html", context)
