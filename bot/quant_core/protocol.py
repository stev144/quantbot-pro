# ============================================================
# bot/quant_core/protocol.py
# claude code changed: new file — Stage 2D Rev B step 1: the canonical
# protocol configuration.
#
# One frozen, versioned object holds every protocol value that research and
# live must share. Engines read these values from here instead of keeping
# their own defaults. A changed value produces a different config hash, and a
# deliberate methodology change must bump METHODOLOGY_VERSION so old results
# cannot masquerade as results of the new protocol.
#
# Values are protocol BASELINES chosen before evaluation — none of them is
# claimed to be empirically optimal (see docs/STAGE2D_CANONICAL_PROTOCOL.md,
# assumption register Q1–Q20). Values the owner has not yet given are None and
# every consumer must fail closed on them.
# ============================================================

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Optional

from bot.quant_core.provenance import config_hash

METHODOLOGY_VERSION = "STAGE2D-REVB-3"   # claude code changed: Stage 2D Rev B D-1..D-5 — was REVB-2; bumped for L = 1000, Design A seed placement and the provenance requirements (a REVB-2 seed/result can never validate as REVB-3)   # claude code changed: Stage 2D Rev B Steps 9–11 — was REVB-1; bumped because the trade lifecycle (C1/C2, H = 24) and seed semantics (causal init, C6) changed, so REVB-1 results/seeds can never pass as REVB-2


class ProtocolValueMissing(RuntimeError):
    """A consumer needs a protocol value the owner has not frozen yet."""


@dataclass(frozen=True)
class CanonicalProtocol:
    # Q1 — Kalman (random-walk state θ = [β, α]′); baseline, not optimised
    q_beta: float = 1e-5
    q_alpha: float = 1e-5
    r: float = 1e-3
    p0_scale: float = 1.0                    # P0 = p0_scale · I at (re)seed

    # Q2 — causal z-score of the innovation, on the hourly grid
    z_window_candles: int = 504
    z_min_valid: int = 168
    z_clip: float = 5.0
    warmup_candles: int = 168                # candles after a (re)seed before any signal

    # Q3 — entry
    entry_threshold: float = 2.0             # |z| ≥ entry_threshold

    # Q4/Q5 — exits: held-spread normalisation or time stop; no partial exit,
    # no target exit, no z stop-loss (owner D4/D10)
    partial_exit: bool = False

    # Q6 — H: maximum ENTRY-FILL → EXIT-FILL horizon in candles (owner D3).
    # claude code changed: Stage 2D Rev B decision lock — H = 24 frozen as the
    # initial canonical BASELINE (not optimised, not evidence). Was None.
    max_holding_candles: Optional[int] = 24

    # claude code changed: Stage 2D Rev B C6 — causal Kalman initialisation:
    # θ₀ = OLS on the init window [I_s, I_s + L − 1] ONLY; the filter starts at
    # the next grid candle.
    # claude code changed: owner lock 2026-10-02 — L = 1000 hourly candles is a
    # FROZEN BASELINE CONVENTION (STAGE2D_PRE_STEP12_AUDIT.md §3). It is NOT an
    # optimised trading parameter, NOT claimed mathematically unique, and NOT
    # chosen from P&L/IC/Sharpe/win rate. L = 504 is a development sensitivity
    # check only. Changing L requires a METHODOLOGY_VERSION change.
    init_window_candles: Optional[int] = 1000

    # claude code changed: owner lock 2026-10-02 — Design A: the init window
    # lies entirely BEFORE the selection zone; the selection zone starts at the
    # seed candle. Init observations are initialisation data, not evidence; the
    # Kalman state keeps a fading (measured, nonzero) dependence on them.
    seed_design: str = "A_PRE_SELECTION"

    # Q7 — execution delay: decision at cutoff t, fill at close of t + d
    execution_delay_candles: int = 1

    # Q19 — gap policy (owner D8)
    gap_max_candles: int = 3

    # Q17 — fixed-fraction sizing (owner): T = f · strategy capital, f ≤ f_max
    pair_allocation_fraction: float = 0.05
    pair_allocation_hard_max: float = 0.05   # owner said "configurable hard maximum" without a value → set equal to f (no headroom)

    # Q14 — permutation baseline (owner); timing null only
    permutation_b: int = 999

    # Q10/Q20 — legacy values kept, NOT validated (owner has not frozen them)
    coint_alpha: float = 0.05
    fdr_method: str = "BH"
    fdr_q: float = 0.05

    def __post_init__(self):
        for name in ("q_beta", "q_alpha", "r", "p0_scale", "z_clip", "entry_threshold",
                     "pair_allocation_fraction", "pair_allocation_hard_max", "coint_alpha", "fdr_q"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be a finite positive number, got {v!r}")
        for name in ("z_window_candles", "z_min_valid", "warmup_candles", "execution_delay_candles",
                     "gap_max_candles", "permutation_b"):
            v = getattr(self, name)
            if not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
                raise ValueError(f"{name} must be an integer >= 1, got {v!r}")
        if self.z_min_valid > self.z_window_candles:
            raise ValueError("z_min_valid cannot exceed z_window_candles")
        if self.pair_allocation_fraction > self.pair_allocation_hard_max or self.pair_allocation_hard_max > 1:
            raise ValueError("require pair_allocation_fraction <= pair_allocation_hard_max <= 1")
        if self.max_holding_candles is not None and not (
                isinstance(self.max_holding_candles, int) and self.max_holding_candles > self.execution_delay_candles):
            # D3: exit decision at entry + H − d must come after the entry fill
            raise ValueError("max_holding_candles must be an integer > execution_delay_candles")
        if self.init_window_candles is not None and not (
                isinstance(self.init_window_candles, int) and not isinstance(self.init_window_candles, bool)
                and self.init_window_candles >= 2):   # claude code changed: Stage 2D Rev B C6 — an OLS of 2 parameters needs ≥ 2 points
            raise ValueError("init_window_candles must be an integer >= 2")
        if self.seed_design != "A_PRE_SELECTION":   # claude code changed: owner lock — the only canonical seed placement
            raise ValueError("seed_design must be 'A_PRE_SELECTION' (owner lock, Design A)")
        if self.partial_exit:
            raise ValueError("partial exits are not part of the canonical Stage 2D strategy (owner D4)")

    # ── derived quantities (D3: exit fill = entry fill + H) ──────────────────
    def require_max_holding(self) -> int:
        if self.max_holding_candles is None:
            raise ProtocolValueMissing("H (max_holding_candles) has not been frozen by the owner (D3 gives "
                                       "its meaning, not its value)")
        return self.max_holding_candles

    def require_init_window(self) -> int:   # claude code changed: Stage 2D Rev B C6
        if self.init_window_candles is None:
            raise ProtocolValueMissing("init_window_candles (causal Kalman init window, C6) has not been frozen by the owner")
        return self.init_window_candles

    def time_stop_decision_offset(self) -> int:
        """Candles after the entry fill at which the time-stop decision is taken
        so that the exit fill lands exactly at entry + H:  H − d."""
        return self.require_max_holding() - self.execution_delay_candles

    def as_dict(self) -> dict:
        d = asdict(self)
        d["methodology_version"] = METHODOLOGY_VERSION
        return d

    def config_hash(self) -> str:
        d = self.as_dict()   # claude code changed: Stage 2D Rev B — every Optional value hashes explicitly ("UNSET"), so an unset value can never collide with a set one
        return config_hash({k: ("UNSET" if v is None else v) for k, v in d.items()})

    def with_overrides(self, **kw) -> "CanonicalProtocol":
        """A different configuration (e.g. a later Q/R grid point). Its hash
        differs, so results never mix with the baseline's."""
        return replace(self, **kw)


CANONICAL = CanonicalProtocol()
