# ============================================================
# bot/quant_core/cointegration.py
# claude code changed: new file — Stage 2D Phase E, Stage 2C §5.
#
# Pair selection test = Engle–Granger: OLS of x on y with an intercept on the
# selection zone, then ADF on the residual judged with MacKinnon
# COINTEGRATION critical values / p-values (statsmodels.coint). Ordinary DF
# p-values are invalid for this decision because β̂ is estimated to minimise
# residual variance, which biases the residual toward looking stationary.
#
# If EG cannot be computed the pair is UNTESTABLE — no other test is
# substituted. Inputs must be a gap-free segment (no NaN): the test never runs
# across a missing candle.
#
# Persistence (stage 2) = ADF on x − α̂ − β̂ y over the VALIDATION zone with
# α̂, β̂ frozen from selection; ordinary DF tables are valid there because no
# coefficient is estimated on that data.
# ============================================================

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from bot.quant_core.status import Result, Status, bad, ok


@dataclass(frozen=True)
class EGTest:
    pvalue: float          # MacKinnon cointegration p-value
    statistic: float
    alpha: float           # OLS intercept (selection zone)
    beta: float            # OLS hedge ratio (selection zone)
    n_obs: int


def _clean_pair(x: pd.Series, y: pd.Series, min_obs: int) -> Result:
    if len(x) != len(y):
        return bad(Status.UNTESTABLE, "x and y have different lengths")
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if not (np.isfinite(xa).all() and np.isfinite(ya).all()):
        return bad(Status.UNTESTABLE, "segment contains NaN/inf (gaps must be resolved before testing)")
    if len(xa) < min_obs:
        return bad(Status.UNTESTABLE, f"{len(xa)} observations < min_obs={min_obs}")
    if np.var(ya) == 0 or np.var(xa) == 0:
        return bad(Status.UNTESTABLE, "constant series")
    return ok((xa, ya))


def ols_hedge(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """(α̂, β̂) from OLS of x on [1, y]."""
    X = np.column_stack([np.ones(len(y)), y])
    (a, b), *_ = np.linalg.lstsq(X, x, rcond=None)
    return float(a), float(b)


def engle_granger(x: pd.Series, y: pd.Series, *, min_obs: int, maxlag: Optional[int],
                  autolag: Optional[str]) -> Result:
    """Engle–Granger test of x on y (constant trend). Returns OK(EGTest) or
    UNTESTABLE — never a p-value from a different test."""
    clean = _clean_pair(x, y, min_obs)
    if not clean.ok:
        return clean
    xa, ya = clean.value
    from statsmodels.tsa.stattools import coint
    try:
        stat, pvalue, _crit = coint(xa, ya, trend="c", maxlag=maxlag, autolag=autolag)
    except Exception as e:   # numerical failure inside the test → untestable, by design
        return bad(Status.UNTESTABLE, f"Engle–Granger failed: {type(e).__name__}: {e}")
    if not (np.isfinite(stat) and np.isfinite(pvalue)):
        return bad(Status.UNTESTABLE, "Engle–Granger returned a non-finite statistic/p-value")
    a, b = ols_hedge(xa, ya)
    return ok(EGTest(float(pvalue), float(stat), a, b, len(xa)))


def persistence_adf(x_val: pd.Series, y_val: pd.Series, *, alpha_hat: float, beta_hat: float,
                    min_obs: int, maxlag: Optional[int], autolag: Optional[str]) -> Result:
    """ADF p-value of the validation-zone residual built with FROZEN selection
    coefficients. OK(pvalue) or UNTESTABLE."""
    if not (np.isfinite(alpha_hat) and np.isfinite(beta_hat)):
        return bad(Status.UNTESTABLE, "non-finite frozen coefficients")
    clean = _clean_pair(x_val, y_val, min_obs)
    if not clean.ok:
        return clean
    xa, ya = clean.value
    from statsmodels.tsa.stattools import adfuller
    resid = xa - alpha_hat - beta_hat * ya
    try:
        res = adfuller(resid, maxlag=maxlag, autolag=autolag, regression="c")
    except Exception as e:
        return bad(Status.UNTESTABLE, f"ADF failed: {type(e).__name__}: {e}")
    p = float(res[1])
    if not np.isfinite(p):
        return bad(Status.UNTESTABLE, "ADF returned a non-finite p-value")
    return ok(p)


# ── pair-family hygiene ──────────────────────────────────────────────────────

class DuplicateRelationshipError(ValueError):
    """The same unordered pair appears more than once (e.g. both orientations)."""


def relationship_key(symbol_a: str, symbol_b: str) -> Tuple[str, str]:
    """Orientation-free identity of a pair relationship."""
    if symbol_a == symbol_b:
        raise ValueError("a pair needs two different symbols")
    return tuple(sorted((symbol_a, symbol_b)))   # type: ignore[return-value]


def assert_no_duplicate_relationships(pairs: Iterable[Tuple[str, str]]) -> None:
    seen: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for a, b in pairs:
        key = relationship_key(a, b)
        if key in seen:
            raise DuplicateRelationshipError(
                f"relationship {key} appears twice: {seen[key]} and {(a, b)}"
            )
        seen[key] = (a, b)


def shared_leg_map(pairs: Iterable[Tuple[str, str]]) -> Dict[str, List[Tuple[str, str]]]:
    """asset → every pair it participates in (dependence/concentration record)."""
    out: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for a, b in pairs:
        out[a].append((a, b))
        out[b].append((a, b))
    return dict(out)
