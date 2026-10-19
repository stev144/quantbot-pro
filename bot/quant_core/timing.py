# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/timing.py
#
# claude code changed: new file — 2026-10-03, remediation F-04 (zero-latency
# same-close labels). ONE reusable timing contract for research labels. Not
# yet wired into the cross-sectional / contagion / feature engines: the
# canonical execution delay d for those programs is an OWNER DECISION, so the
# contract has NO default delay — a caller must state it.
#
# THE FIVE TIMES (bar t = the candle with open time T_t, closing at T_t + Δ)
#   INFORMATION TIME   T_t + Δ      the close of bar t: everything in bars ≤ t is known
#   SIGNAL TIME        T_t + Δ      the signal is computed from bars ≤ t
#   ORDER TIME         T_t + Δ      the order is sent (no earlier than the signal)
#   EXECUTION TIME     open of bar t + d  (d >= 1 bars; d = 0 is the legacy same-close
#                      assumption and is allowed ONLY as an explicitly labelled
#                      diagnostic, never as canonical)
#   LABEL              the return earned from the execution price to the exit
#                      price h bars later: P_exec(t+d+h) / P_exec(t+d) − 1
#
# A label built with d = 0 and close prices (close_t → close_{t+h}) assumes the
# trade fills at the very print that formed the signal — the F-04 defect.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

EXECUTION_PRICES = ("open", "close")


@dataclass(frozen=True)
class TimingContract:
    execution_delay_bars: int            # d — owner decision; no default on purpose
    execution_price: str = "open"        # price column the order fills at, bar t+d
    allow_same_close_diagnostic: bool = False

    def __post_init__(self):
        if self.execution_price not in EXECUTION_PRICES:
            raise ValueError(f"execution_price must be one of {EXECUTION_PRICES}")
        if not isinstance(self.execution_delay_bars, int) or self.execution_delay_bars < 0:
            raise ValueError("execution_delay_bars must be a non-negative integer")
        if self.execution_delay_bars == 0 and not self.allow_same_close_diagnostic:
            raise ValueError("d = 0 (fill at the signal print) is the F-04 defect; allowed only with "
                             "allow_same_close_diagnostic=True and never as canonical")
        if self.execution_delay_bars == 0 and self.execution_price == "open":
            raise ValueError("d = 0 with open prices would fill BEFORE the signal close — impossible")

    @property
    def label_name(self) -> str:
        return f"fwd_ret_{self.execution_price}_d{self.execution_delay_bars}"


# claude code changed: 2026-10-03 owner decision D-1 (DECISION_SHEET_D1-D11_2026-10-03.md, signed) — the
# canonical execution timing for EVERY research label (feature, cross-sectional, contagion, derivatives, PCA):
# signal at bar t's close, fill at bar t+1's OPEN (d = 1). d = 2 is a sensitivity check only, never used for
# selection; d = 0 exists only as an explicitly labelled diagnostic (allow_same_close_diagnostic).
RESEARCH_LABEL_CONTRACT = TimingContract(execution_delay_bars=1, execution_price="open")
SENSITIVITY_CONTRACT_D2 = TimingContract(execution_delay_bars=2, execution_price="open")
LABEL_TIMING_BASIS = "D1_SIGNAL_CLOSE_t__FILL_OPEN_t+1"


def research_forward_return(df: pd.DataFrame, horizon: int,
                            contract: TimingContract = RESEARCH_LABEL_CONTRACT) -> pd.Series:
    """The research label under the D-1 contract: P_open(t+1+h) / P_open(t+1) − 1. Fails closed when the
    frame has no `open` column — there is deliberately no fallback to close (that would silently revert to
    the zero-latency label F-04 / D-1 forbids)."""
    if contract.execution_price not in df.columns:
        raise ValueError(f"research label needs a '{contract.execution_price}' column (D-1: fill at the next "
                         f"bar's open) — no fallback to close")
    return forward_label(df, horizon, contract)


def forward_label(df: pd.DataFrame, horizon: int, contract: TimingContract) -> pd.Series:
    """Forward return earned by a position decided at bar t's close, filled at
    bar t+d's execution price and exited h bars after the fill:
        label_t = P(t+d+h) / P(t+d) − 1,   P = df[contract.execution_price].
    NaN where the future bars do not exist. Row-based: the caller must pass a
    gap-free grid (or accept that a gap shifts the wall-clock horizon)."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    p = df[contract.execution_price].astype(float)
    d = contract.execution_delay_bars
    return (p.shift(-(d + horizon)) / p.shift(-d) - 1.0).rename(f"{contract.label_name}_h{horizon}")
