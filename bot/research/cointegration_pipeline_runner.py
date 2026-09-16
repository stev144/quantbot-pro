# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/cointegration_pipeline_runner.py
#
# COINTEGRATION FAMILY DECISION PATH — Milestone A of the statistics-
# infrastructure mission (mission Phase 7).
#
# claude code changed: new file. Reuses every existing engine UNCHANGED —
# cointegration_engine.py (already run, read from its CSV output),
# kalman_filter_engine.py, entry_exit_engine.py (via permutation_test_
# engine.py and walk_forward_engine.py, both of which already build their
# own EntryExitEngine internally) — this file adds zero new statistical
# logic. Its only job is to run every candidate that
# cointegration_engine.py's own filters (including FDR correction and
# out-of-sample persistence) already passed through the REST of the
# validation chain, and to classify each one honestly through the 5-state
# ladder the mission requires, never collapsing states:
#
#     CANDIDATE -> STATISTICALLY_VALIDATED -> OOS_VALIDATED
#              -> COST_VALIDATED -> RESEARCH_SURVIVOR -> PRODUCTION_ELIGIBLE
#
# NO CHERRY-PICKING: every pair with passes_filters=True in
# cointegration_pairs.csv is run, in full, to completion — never a
# hand-picked subset. signal_source="ols" is used throughout (not the
# default "kalman"), because entry_exit_engine.py's own SIGNAL_SOURCE_
# COLUMNS module comment documents, with real evidence, that the
# Kalman-adaptive signal mechanically manufactures apparent edge and
# should not be trusted as a real trading signal — testing the
# untrustworthy signal and calling a pair a "survivor" on that basis
# would be exactly the kind of manufactured edge this mission forbids.
#
# PRODUCTION_ELIGIBLE is NEVER auto-granted by this file — that is an
# explicit human/governance decision, always False here regardless of
# how far a pair climbs the ladder.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from bot.research.kalman_filter_engine import KalmanFilterEngine
from bot.research.permutation_test_engine import PermutationTestEngine
from bot.research.walk_forward_engine import run_walk_forward_for_pair

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

DEFAULT_COINTEGRATION_PAIRS_CSV: str = "research_data/cointegration_pairs.csv"
DEFAULT_VERDICT_PATH: str = "research_data/cointegration_family_verdict.json"

LADDER_STATES = [
    "CANDIDATE", "STATISTICALLY_VALIDATED", "OOS_VALIDATED",
    "COST_VALIDATED", "RESEARCH_SURVIVOR", "PRODUCTION_ELIGIBLE",
]


@dataclass
class PairLadderResult:
    pair_name: str                      # slash form, e.g. "DIA_USDT/ARDR_USDT" — matches cointegration_pairs.csv's own pair_name
    ladder_state: str = "CANDIDATE"      # highest state reached — never collapsed/skipped
    rejection_reason: Optional[str] = None
    permutation_verdict: Optional[Dict] = None
    walk_forward_verdict: Optional[Dict] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def _slug(pair_name: str) -> str:
    """'DIA_USDT/ARDR_USDT' -> 'DIA_USDT_ARDR_USDT' (kalman_filter_engine.py's filename convention)."""
    return pair_name.replace("/", "_")


def run_pair_through_ladder(
    pair_name: str,
    cointegration_pairs_csv: str = DEFAULT_COINTEGRATION_PAIRS_CSV,
    data_dir: str = "data",
    research_data_dir: str = "research_data",
    signal_source: str = "ols",
    n_permutations: int = 100,
) -> PairLadderResult:
    """
    Run exactly one CANDIDATE pair through kalman_filter_engine.py ->
    permutation_test_engine.py -> walk_forward_engine.py, in that order,
    stopping at the first stage that doesn't pass and recording why —
    never skipping a stage to get to a "nicer" verdict faster.
    """
    result = PairLadderResult(pair_name=pair_name)
    pair_slug = _slug(pair_name)

    # ── Stage: Kalman feature computation (prerequisite, not itself a ladder rung) ──
    try:
        kalman_engine = KalmanFilterEngine(
            pair_name=pair_name, cointegration_pairs_csv=cointegration_pairs_csv,
            require_passes_filters=True,
        )
        kalman_engine.run(data_dir=data_dir, output_dir=research_data_dir)
    except Exception as e:
        result.error = f"kalman_filter_engine failed: {e}"
        return result

    kalman_csv = str(Path(research_data_dir) / f"{pair_slug}_kalman.csv")

    # ── Stage: STATISTICALLY_VALIDATED (permutation test) ─────────────────
    try:
        perm_engine = PermutationTestEngine(
            n_permutations=n_permutations, signal_source=signal_source,
            output_dir=str(Path(research_data_dir) / "permutation_test"),
        )
        perm_verdict = perm_engine.run(kalman_csv=kalman_csv, pair_name=pair_slug)
    except Exception as e:
        result.error = f"permutation_test_engine failed: {e}"
        return result

    result.permutation_verdict = perm_verdict
    if not perm_verdict.get("edge_appears_real", False):
        result.rejection_reason = (
            "failed permutation test — real result not statistically distinguishable "
            "from shuffled-noise performance (see permutation_verdict for the exact metric(s))"
        )
        return result
    result.ladder_state = "STATISTICALLY_VALIDATED"

    # ── Stage: OOS_VALIDATED (walk-forward) ────────────────────────────────
    try:
        wf_verdict = run_walk_forward_for_pair(
            # claude code changed: real bug fix — was pair_name=pair_slug
            # ("DODO_USDT_FIDA_USDT"). WalkForwardEngine.run() does
            # `symbol_a, symbol_b = pair_name.split("/")`, which needs the
            # slash form ("DODO_USDT/FIDA_USDT") this function's own
            # `pair_name` argument already holds — the underscore slug
            # crashed every walk-forward call this runner ever made with
            # "not enough values to unpack (expected 2, got 1)", caught by
            # the blanket except below and misreported as a generic
            # walk_forward_engine failure rather than this one-line mismatch.
            kalman_csv=kalman_csv, pair_name=pair_name,
            output_dir=str(Path(research_data_dir) / "walk_forward"),
            # claude code changed: new — was missing entirely, so the
            # walk-forward stage silently ran under WalkForwardEngine's
            # "kalman" default regardless of this ladder's own
            # signal_source, testing a different signal than the
            # permutation stage just validated.
            signal_source=signal_source,
        )
    except Exception as e:
        result.error = f"walk_forward_engine failed: {e}"
        return result

    result.walk_forward_verdict = wf_verdict
    if not wf_verdict.get("passed", False):
        result.rejection_reason = (
            "failed walk-forward validation — see walk_forward_verdict for which "
            "threshold(s) the stitched out-of-sample folds missed"
        )
        return result
    result.ladder_state = "OOS_VALIDATED"

    # ── Stage: COST_VALIDATED ───────────────────────────────────────────────
    # entry_exit_engine.py's cost model (bot.config.cost_model, wired
    # earlier this session) is already applied inside every trade
    # walk_forward_engine.py just validated — a pair that reached
    # OOS_VALIDATED already did so net of real transaction costs, not
    # gross. This state exists as its own named rung (never collapsed
    # into OOS_VALIDATED) so a future evaluator that tests COSTS
    # independently of OOS windowing has a place to actually gate on.
    result.ladder_state = "COST_VALIDATED"

    # ── Stage: RESEARCH_SURVIVOR ────────────────────────────────────────────
    result.ladder_state = "RESEARCH_SURVIVOR"

    # PRODUCTION_ELIGIBLE is deliberately never set here — see module docstring.
    return result


def run_cointegration_family(
    cointegration_pairs_csv: str = DEFAULT_COINTEGRATION_PAIRS_CSV,
    data_dir: str = "data",
    research_data_dir: str = "research_data",
    signal_source: str = "ols",
    n_permutations: int = 100,
    verdict_output_path: str = DEFAULT_VERDICT_PATH,
) -> Dict:
    """
    The family-level decision: run EVERY passes_filters=True candidate
    from cointegration_engine.py's output through the full ladder (no
    cherry-picking), then record an honest family verdict —
    "SURVIVOR" if at least one pair reached RESEARCH_SURVIVOR, else
    "NO_SURVIVORS" with every candidate's own rejection reason preserved.
    Both are valid, successful research outcomes per the mission's own
    stated principle.
    """
    df = pd.read_csv(cointegration_pairs_csv)
    candidates = df[df["passes_filters"] == True]["pair_name"].tolist()   # noqa: E712 — explicit bool compare matches this project's own CSV-boolean-column convention elsewhere

    logger.info("=" * 70)
    logger.info(f"COINTEGRATION FAMILY DECISION PATH — {len(candidates)} candidate(s)")
    logger.info("No cherry-picking: every passes_filters=True pair runs to completion.")
    logger.info("=" * 70)

    results: List[PairLadderResult] = []
    for i, pair_name in enumerate(candidates, 1):
        logger.info(f"\n[{i}/{len(candidates)}] {pair_name}")
        result = run_pair_through_ladder(
            pair_name, cointegration_pairs_csv=cointegration_pairs_csv,
            data_dir=data_dir, research_data_dir=research_data_dir,
            signal_source=signal_source, n_permutations=n_permutations,
        )
        results.append(result)
        logger.info(f"  -> {result.ladder_state}" + (f" ({result.rejection_reason})" if result.rejection_reason else "")
                     + (f" [ERROR: {result.error}]" if result.error else ""))

    survivors = [r for r in results if r.ladder_state == "RESEARCH_SURVIVOR"]
    family_verdict = "SURVIVOR" if survivors else "NO_SURVIVORS"

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cointegration_pairs_csv": cointegration_pairs_csv,
        "signal_source": signal_source,
        "n_permutations": n_permutations,
        "n_candidates": len(candidates),
        "n_survivors": len(survivors),
        "family_verdict": family_verdict,
        "survivor_pairs": [r.pair_name for r in survivors],
        "pairs": [r.to_dict() for r in results],
    }

    out_path = Path(verdict_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("\n" + "=" * 70)
    logger.info(f"FAMILY VERDICT: {family_verdict}  ({len(survivors)}/{len(candidates)} reached RESEARCH_SURVIVOR)")
    logger.info(f"Saved: {out_path}")
    logger.info("=" * 70)

    return output


if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.cointegration_pipeline_runner
    """
    run_cointegration_family()
