# ============================================================
# bot/pairs/health.py
# claude code changed: new file — live pairs-trading pilot, beta-drift
# governance audit (2026-09-24).
#
# THE PROBLEM THIS SOLVES
#
# cointegration_engine.py's passes_filters gate (read into PairConfig at
# config-load time — see bot/pairs/config.py) only checks the STATIC,
# full-history relationship, computed once. It says nothing about whether
# the online Kalman filter's CURRENTLY-tracked hedge ratio still
# resembles that seed. Confirmed real gap (2026-09-24): MINA/ONG's
# recent-data beta flipped sign relative to its seed (+1.625 -> -0.307)
# while the pair still passed the static test — the static gate and the
# live online state can disagree, and nothing was watching the live side
# before this module.
#
# THIS IS THE SINGLE AUTHORITATIVE SOURCE for a pair's live health —
# pairs_bot_runner.py, the dashboard, and any future consumer all call
# evaluate_pair_health() rather than each re-implementing their own
# drift logic. See docs/pairs_health_audit (the published audit report)
# for the full historical-evidence investigation behind the specific
# thresholds chosen here.
#
# WHY THE THRESHOLD IS NOT A SIMPLE "50% = BAD" RULE
#
# An earlier, hastily-built version of this guard blocked any entry with
# >50% beta drift or a sign flip. Cross-referencing every historical
# trade's real entry_beta (research_data/{PAIR}_trade_log.csv) against
# drift-from-seed showed that was NOT empirically justified:
#   - DODO/FIDA: 118/335 trades (35%) had >50% drift at entry — win rate
#     84.7% vs. 88.5% for "normal" trades, mean P&L actually HIGHER
#     (+3.33% vs +3.18%).
#   - MINA/ONG: 210/351 trades (60%), including 15 with a SIGN-FLIPPED
#     entry_beta — win rate 81.9% vs 85.8%, mean P&L again slightly
#     higher (+2.89% vs +2.55%).
# Large drift, even sign flips, did not historically predict worse trade
# outcomes for these two pairs. BETA_DRIFT_DEGRADED_RATIO below is set
# well above 50% and is explicitly marked PROVISIONAL — it is a
# monitoring reference, not a number proven to separate good trades from
# bad ones. A sign flip is still always flagged DEGRADED regardless of
# this economic evidence (see BETA_SIGN_REVERSAL below) — it is treated
# as a mandatory integrity/governance flag pending more data, not
# because the historical P&L evidence shows it is dangerous (it does
# not, so far, on n=15 trades — too small to conclude either way).
#
# WHAT THIS MODULE DELIBERATELY DOES NOT DO
#
# - Does not force-close an existing open position because health
#   degrades mid-trade — that is a separate, unresearched decision (see
#   the audit report's "remaining risks"). Callers are expected to
#   surface a warning on open positions instead (see
#   PairsExecutionEngine/dashboard integration).
# - Does not compute a single weighted "score" — each check is its own
#   named, independently-inspectable HealthCheck; the overall state is
#   the worst of the individual checks (NORMAL < DEGRADED < INVALIDATED),
#   never a blended number that hides which specific thing is wrong.
# ============================================================

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple

# claude code changed: new — the live bot (bot/core/pairs_bot_runner.py)
# and the dashboard (bot/views/pairs_pilot_data.py) are separate OS
# processes with no shared memory, so a health result computed inside
# the bot's loop cannot be read directly by a Django request. This file
# is a small, durable snapshot the bot writes every heartbeat and the
# dashboard reads — deliberately a plain JSON file next to the bot's own
# log file (ephemeral, frequently-overwritten monitoring data, not a
# permanent trade record that belongs in the database) rather than a new
# DB model/migration for what is fundamentally "current status," not
# history.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEALTH_SNAPSHOT_PATH = os.path.join(_PROJECT_ROOT, "logs", "pairs_health_snapshot.json")

# claude code changed: if the snapshot file is older than this, the
# dashboard must treat it as stale (bot not running / crashed / hung)
# rather than silently showing a last-known-good state as if it were
# current — item 20's "fail closed when data is obviously stale."
SNAPSHOT_STALE_AFTER_SECONDS = 3 * 3600  # 3x the bot's own hourly candle interval — generous, but not indefinite


class PairHealthState(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    INVALIDATED = "INVALIDATED"


_SEVERITY = {PairHealthState.NORMAL: 0, PairHealthState.DEGRADED: 1, PairHealthState.INVALIDATED: 2}

# claude code changed: policy_version stamps every PairHealthResult so a
# log/dashboard record can always be traced back to which rule set
# produced it — required when (not if) these PROVISIONAL numbers get
# recalibrated later.
POLICY_VERSION = "2026-09-24.1-provisional"

# PROVISIONAL — see module docstring above. NOT empirically shown to
# predict worse trade outcomes at this level; set well above the 50%
# figure historical trades actually cleared routinely. Purely a
# monitoring/DEGRADED reference until a larger, dedicated study (more
# pairs, more regimes) either tightens or removes it.
BETA_DRIFT_DEGRADED_RATIO = 1.5


@dataclass(frozen=True)
class HealthCheck:
    """One named, independently-inspectable check. metric/current_value/
    reference/status/reason together are the full audit trail for why
    this check landed where it did — never collapsed into a score."""
    metric: str
    current_value: object
    reference: object
    status: PairHealthState
    reason: str


@dataclass(frozen=True)
class PairHealthResult:
    pair_name: str
    state: PairHealthState              # worst of `checks` — the one number callers act on
    seed_beta: float
    current_beta: float
    beta_drift_ratio: float
    beta_sign_reversed: bool
    checks: Tuple[HealthCheck, ...]
    reason_codes: Tuple[str, ...]       # metric names of every non-NORMAL check, for compact logging
    evaluated_at: datetime
    policy_version: str = POLICY_VERSION

    def summary(self) -> str:
        if self.state == PairHealthState.NORMAL:
            return "NORMAL"
        reasons = "; ".join(c.reason for c in self.checks if c.status != PairHealthState.NORMAL)
        return f"{self.state.value} ({reasons})"


def _check_cointegration_gate(passes_filters: bool, reject_reason: str) -> HealthCheck:
    # claude code changed: the one check with a strong, direct evidentiary
    # basis — this IS the ADF/OOS-persistence/FDR framework this whole
    # project's research pipeline is built on, not a new invention.
    if passes_filters:
        return HealthCheck("cointegration_gate", True, True, PairHealthState.NORMAL, "passes static cointegration + out-of-sample persistence")
    return HealthCheck(
        "cointegration_gate", False, True, PairHealthState.INVALIDATED,
        f"fails static cointegration test: {reject_reason or 'not cointegrated'}",
    )


def _check_beta_sign(seed_beta: float, current_beta: float) -> Tuple[HealthCheck, bool]:
    # claude code changed: mandatory special case (see module docstring)
    # — always DEGRADED on a sign flip regardless of the drift-magnitude
    # economic evidence, since n=15 historical sign-flipped trades is too
    # small a sample to call this either safe or dangerous in general.
    if seed_beta == 0:
        return HealthCheck("beta_sign", current_beta, seed_beta, PairHealthState.NORMAL, "seed beta is 0 — sign comparison not meaningful"), False
    reversed_ = (seed_beta > 0) != (current_beta > 0)
    if reversed_:
        return HealthCheck(
            "beta_sign", current_beta, seed_beta, PairHealthState.DEGRADED,
            f"BETA_SIGN_REVERSAL — live beta ({current_beta:.4f}) has flipped sign vs seed ({seed_beta:.4f})",
        ), True
    return HealthCheck("beta_sign", current_beta, seed_beta, PairHealthState.NORMAL, "sign consistent with seed"), False


def _check_beta_drift_magnitude(seed_beta: float, current_beta: float) -> Tuple[HealthCheck, float]:
    if seed_beta == 0:
        return HealthCheck("beta_drift_magnitude", current_beta, seed_beta, PairHealthState.NORMAL, "seed beta is 0 — drift ratio not meaningful"), 0.0
    drift_ratio = abs(current_beta - seed_beta) / abs(seed_beta)
    if drift_ratio > BETA_DRIFT_DEGRADED_RATIO:
        return HealthCheck(
            "beta_drift_magnitude", drift_ratio, BETA_DRIFT_DEGRADED_RATIO, PairHealthState.DEGRADED,
            f"{drift_ratio:.0%} drift from seed exceeds the PROVISIONAL {BETA_DRIFT_DEGRADED_RATIO:.0%} monitoring reference "
            f"(not proven to predict worse trades — see module docstring)",
        ), drift_ratio
    return HealthCheck("beta_drift_magnitude", drift_ratio, BETA_DRIFT_DEGRADED_RATIO, PairHealthState.NORMAL, "within historically-observed range"), drift_ratio


def evaluate_pair_health(
    pair_name: str,
    seed_beta: float,
    current_beta: float,
    passes_filters: bool,
    reject_reason: str = "",
) -> PairHealthResult:
    """The single authoritative health evaluation for one pair. Pure
    function of its inputs — no I/O, no global state — so callers
    (pairs_bot_runner.py's heartbeat/entry gate, the dashboard, tests)
    all get identical results from identical inputs, and this is trivial
    to unit test exhaustively (see bot/tests/test_pairs_health.py)."""
    coint_check = _check_cointegration_gate(passes_filters, reject_reason)
    sign_check, sign_reversed = _check_beta_sign(seed_beta, current_beta)
    drift_check, drift_ratio = _check_beta_drift_magnitude(seed_beta, current_beta)

    checks = (coint_check, sign_check, drift_check)
    overall = max((c.status for c in checks), key=lambda s: _SEVERITY[s])
    reason_codes = tuple(c.metric.upper() for c in checks if c.status != PairHealthState.NORMAL)

    return PairHealthResult(
        pair_name=pair_name,
        state=overall,
        seed_beta=seed_beta,
        current_beta=current_beta,
        beta_drift_ratio=drift_ratio,
        beta_sign_reversed=sign_reversed,
        checks=checks,
        reason_codes=reason_codes,
        evaluated_at=datetime.now(timezone.utc),
    )


def _result_to_dict(h: PairHealthResult) -> dict:
    return {
        "pair_name": h.pair_name,
        "state": h.state.value,
        "seed_beta": h.seed_beta,
        "current_beta": h.current_beta,
        "beta_drift_ratio": h.beta_drift_ratio,
        "beta_sign_reversed": h.beta_sign_reversed,
        "reason_codes": list(h.reason_codes),
        "summary": h.summary(),
        "checks": [
            {"metric": c.metric, "current_value": c.current_value, "reference": c.reference, "status": c.status.value, "reason": c.reason}
            for c in h.checks
        ],
        "evaluated_at": h.evaluated_at.isoformat(),
        "policy_version": h.policy_version,
    }


def write_health_snapshot(results: Dict[str, PairHealthResult]) -> None:
    """Called once per heartbeat by pairs_bot_runner.py. Overwrites the
    whole snapshot file atomically (write to a temp path, then os.replace
    — never leaves the dashboard reading a half-written file)."""
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "pairs": {name: _result_to_dict(h) for name, h in results.items()},
    }
    tmp_path = HEALTH_SNAPSHOT_PATH + ".tmp"
    os.makedirs(os.path.dirname(HEALTH_SNAPSHOT_PATH), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, HEALTH_SNAPSHOT_PATH)


def read_health_snapshot() -> dict:
    """Called by the dashboard (a separate process from the live bot —
    see module docstring). Returns {"available": False, "reason": ...}
    if the bot has never written a snapshot, or if the snapshot is too
    old to trust (item 20: fail closed on stale data rather than show a
    last-known-good state as if it were live)."""
    if not os.path.exists(HEALTH_SNAPSHOT_PATH):
        return {"available": False, "reason": "no health snapshot found — the pairs bot may not have run since this feature was added, or is not currently running"}
    try:
        with open(HEALTH_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"available": False, "reason": f"health snapshot unreadable: {e}"}

    written_at = datetime.fromisoformat(payload["written_at"])
    age_seconds = (datetime.now(timezone.utc) - written_at).total_seconds()
    if age_seconds > SNAPSHOT_STALE_AFTER_SECONDS:
        return {
            "available": False,
            "reason": f"health snapshot is stale ({age_seconds/3600:.1f}h old, last written {written_at.isoformat()}) — the pairs bot may be stopped or hung",
        }

    return {"available": True, "written_at": payload["written_at"], "age_seconds": age_seconds, "pairs": payload["pairs"]}
