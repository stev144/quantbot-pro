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
    LEGACY ladder (claude code changed: 2026-10-04 — legacy Kalman seed F-03, z-timing null F-05, per-fold legacy
    walk-forward; kept for reproducibility only — the evidence path is run_pair_canonical() below).

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


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# claude code changed: 2026-10-04 (step 3) — CANONICAL PAIR LADDER. run_pair_through_ladder() above is the
# LEGACY ladder (legacy Kalman seed F-03, z-timing permutation null F-05, per-fold legacy walk-forward) and is
# kept only so historical runs stay reproducible. run_pair_canonical() is the evidence path:
#
#   1 data        closes truncated to the END OF ZONE V; any sealed-holdout candle is refused (D-8)
#   2 Kalman      KalmanFilterEngine.run_canonical — causal seed at the S start, L = 1000 (REVB-4)
#   3 financing   RECORDED perpetual funding for both legs (borrow row); missing → stop, BORROW_UNAVAILABLE
#   4 lifecycle   EntryExitEngine(rule_set="CANONICAL") — d = 1, H = 24, costs + funding
#   5 endpoint    D-3 primary endpoint: net mean return per trade on label-complete ZONE-V decisions
#   6 null        D-4 Step-17 null — gated: refused until its calibration artifact passes (stage BLOCKED)
#   7 walk-fwd    canonical_walk_forward — fold boundaries are an owner parameter (None → stage BLOCKED)
#   8 verdict     D-7 ladder; a single pair without a confirmed D-3 family is capped at OBSERVATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

CANONICAL_LADDER_VERSION = "QBP-PAIR-LADDER-CANON-1"


def run_pair_canonical(symbol_a: str, symbol_b: str, close_a, close_b, *, output_dir: str,
                       funding_dir: str = "data", fold_edges=None, run_null: bool = True, null_seed: int = 0) -> Dict:
    import functools
    import pandas as pd
    from bot.quant_core.funding import load_funding_series
    from bot.quant_core.research_zones import ZONES
    from bot.quant_core.verdict_ladder import LadderEvidence, ladder_level
    from bot.research.entry_exit_engine import EntryExitEngine
    from bot.research.pairs_step17_null import (Step17NotCalibrated, canonical_net_mean_return_per_trade,
                                                step17_null_test, zone_v_label_complete)
    from bot.research.walk_forward_engine import canonical_walk_forward

    pair = f"{symbol_a}/{symbol_b}"
    out = {"version": CANONICAL_LADDER_VERSION, "pair": pair, "stages": {}, "stopped_at": None}
    stages = out["stages"]

    # 1 — data
    for s in (close_a, close_b):
        ZONES.assert_no_sealed_data(s.index, purpose=f"canonical pair ladder {pair}")
    ca, cb = close_a[close_a.index <= ZONES.V1], close_b[close_b.index <= ZONES.V1]
    stages["data"] = {"status": "OK", "last_candle": str(min(ca.index[-1], cb.index[-1]))}

    # 1b — market profile (claude code changed: 2026-10-04 asset-agnostic). Every later stage applies one asset
    # class's market rules; an incomplete profile (FX: FX_CALENDAR_UNAVAILABLE) stops here — not negative evidence.
    from bot.quant_core.market_profile import AssetClassMismatch, OwnerDecisionMissing, profile_for_pair
    try:
        profile_for_pair(symbol_a, symbol_b).require_canonical(f"canonical pair ladder {pair}")
    except (OwnerDecisionMissing, AssetClassMismatch) as e:
        stages["market"] = {"status": "BLOCKED", "reason": str(e)}
        out["stopped_at"] = "market"
        return out

    # 2 — canonical Kalman
    try:
        eng = KalmanFilterEngine(pair_name=pair, symbol_a=symbol_a, symbol_b=symbol_b, ols_beta=1.0, ols_alpha=0.0)
        eng.run_canonical(ca, cb, output_dir=output_dir)
        stages["kalman"] = {"status": "OK", "seed_timestamp": eng.seed_record.seed_timestamp,
                            "protocol_hash": eng.canonical_protocol.config_hash()}
    except Exception as e:
        stages["kalman"] = {"status": "REFUSED", "reason": str(e)}
        out["stopped_at"] = "kalman"
        return out

    # 3 — recorded funding (fail closed)
    try:
        fin = {"funding_rates_a": load_funding_series(symbol_a, funding_dir),
               "funding_rates_b": load_funding_series(symbol_b, funding_dir)}
        stages["financing"] = {"status": "OK", "source": "recorded perpetual funding"}
    except FileNotFoundError as e:
        stages["financing"] = {"status": "BORROW_UNAVAILABLE", "reason": str(e)}
        out["stopped_at"] = "financing"
        return out
    except AssetClassMismatch as e:   # claude code changed: 2026-10-21 — FX profile complete since 2026-10-16, so FX now reaches this stage; perpetual funding cannot finance FX and the decided MT5 swap source (OD-FX-FS-1) is not implemented here → stop BLOCKED (was an uncaught crash)
        stages["financing"] = {"status": "BLOCKED", "reason": f"{e}; FX swap financing (OD-FX-FS-1) is not implemented in the canonical pairs ladder"}
        out["stopped_at"] = "financing"
        return out

    # 4 — canonical lifecycle
    kc = Path(output_dir) / "canonical" / f"{symbol_a}_{symbol_b}_kalman.csv"
    ee = EntryExitEngine(pair_name=pair, symbol_a=symbol_a, symbol_b=symbol_b, output_dir=output_dir,
                         rule_set="CANONICAL", **fin)
    trade_log, summary, _ = ee.run(kalman_csv=str(kc))
    row = summary.iloc[0]
    stages["lifecycle"] = {"status": "OK", "n_trades": int(len(trade_log)), "provenance_status": row["provenance_status"],
                           "borrow_status": row["borrow_status"], "cost_complete": bool(row["cost_complete"])}

    # 5 — D-3 endpoint on label-complete zone-V decisions
    v = zone_v_label_complete(trade_log)
    endpoint = float(v["net_pnl_pct"].astype(float).mean()) if len(v) else None
    stages["endpoint_zone_v"] = {"status": "OK" if len(v) else "NO_TRADES", "n_trades": int(len(v)),
                                 "net_mean_return_per_trade": endpoint}

    # 6 — Step-17 null (gated)
    null_p = None
    if run_null:
        try:
            stat = functools.partial(canonical_net_mean_return_per_trade, pair_name=pair, symbol_a=symbol_a,
                                     symbol_b=symbol_b, **fin)
            r = step17_null_test(ca, cb, stat, seed=null_seed, canonical=True)
            stages["step17_null"] = r
            null_p = r.get("p_value")
        except Step17NotCalibrated as e:
            stages["step17_null"] = {"status": "BLOCKED", "reason": str(e)}
    else:
        stages["step17_null"] = {"status": "NOT_RUN"}

    # 7 — canonical walk-forward
    if fold_edges is None:
        stages["walk_forward"] = {"status": "BLOCKED", "reason": "fold boundaries are an owner parameter (D-8: inside S ∪ V)"}
    else:
        stages["walk_forward"] = canonical_walk_forward(trade_log, fold_edges)

    # 8 — D-7 ladder (missing inputs cap, never assumed)
    cost_complete = bool(row["cost_complete"])
    level, reasons = ladder_level(LadderEvidence(
        effect_observed=(endpoint > 0) if endpoint is not None else None,
        dependence_aware_test=True if null_p is not None else None,
        family_confirmed=None,                 # only the D-3 registry's Holm confirmation can set this
        net_positive_after_costs=((endpoint > 0) if (endpoint is not None and cost_complete) else None),
        cost_complete=cost_complete,
        causal_execution_d1=True,              # the canonical lifecycle fills at d = 1
        passes_validation_zone=None, capacity_checked=None, provenance_complete=None, forward_holdout_passed=None))
    out["verdict"] = {"level": level, "reasons": reasons}
    return out
