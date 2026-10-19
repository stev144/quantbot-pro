# claude code changed: new file — 2026-10-04. One shared synthetic cointegrated-pair generator for the canonical
# research tests. It replaces five copy-pasted versions (test_step3_canonical_paths, test_asset_agnostic_market,
# test_coint_selection_zone, test_d4_step17_null, test_kalman_seed_lineage) whose structure was identical and whose
# settings differed. The random draws happen in EXACTLY the original order — n normals for leg B's random walk, then
# one normal per candle t = 1 … n−1 for the AR(1) spread — so every call site's data is bit-identical to before
# (fingerprinted before/after the move). Not a test module (no test_ prefix).
import numpy as np
import pandas as pd


def cointegrated_log_pair(n: int, rng: np.random.Generator, *, beta: float = 1.3, alpha: float = 0.1,
                          phi: float = 0.97, rw_sd: float = 0.01, spread_sd: float = 0.004, start: float = 3.0):
    """(log_a, log_b) as numpy arrays: log_b = start + random walk (step sd rw_sd); u = AR(1) with coefficient phi
    and innovation sd spread_sd (u_0 = 0); log_a = beta · log_b + alpha + u. Uses (and advances) the caller's rng,
    so a caller can keep drawing from the same stream afterwards."""
    log_b = np.cumsum(rw_sd * rng.standard_normal(n)) + start
    u = np.zeros(n)
    for t in range(1, n):
        u[t] = phi * u[t - 1] + spread_sd * rng.standard_normal()
    return beta * log_b + alpha + u, log_b


def cointegrated_pair(index: pd.DatetimeIndex, seed: int, **kwargs):
    """(close_a, close_b) Series on `index` from cointegrated_log_pair with a fresh default_rng(seed)."""
    log_a, log_b = cointegrated_log_pair(len(index), np.random.default_rng(seed), **kwargs)
    return pd.Series(np.exp(log_a), index=index), pd.Series(np.exp(log_b), index=index)
