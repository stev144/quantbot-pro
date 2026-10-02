# ============================================================
# bot/quant_core/seed.py
# claude code changed: new file — Stage 2D Rev B step 6 (owner D9): the
# canonical Kalman seed record. Rewritten for decision C6 (causal init).
#
# Research and live must start the filter from the SAME candle with the SAME
# θ₀ and P₀ and then run the same update sequence (D9).
#
# C6 — CAUSAL initialisation. θ₀ is the OLS fit of ln P_A on [ln P_B, 1] over
# the INIT WINDOW [I_s, I_e] (the first L grid candles of the data the run is
# given) and the filter's first step is the next grid candle, I_e + 1 h. No
# candle at or after the seed candle can influence θ₀. The former seed
# (cointegration_pairs.csv's hedge_ratio/intercept — an OLS over the first
# 10,000 candles assigned to candle 0) is exactly the prohibited look-ahead
# and is never written as a canonical seed.
#
# claude code changed: L = CanonicalProtocol.init_window_candles = 1000 (owner
# baseline lock 2026-10-02, not an optimum) and Design A (init window entirely
# before the selection zone; the selection zone starts at the seed candle).
# Every record carries its seed design and the content data version of the
# closes it was produced from (D-5).
#
# Live refuses to trade a pair unless a record exists, is causal, matches the
# current methodology version and protocol hash, AND its θ₀ is reproduced
# from the exchange's own init-window candles (data-version check).
# ============================================================

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from bot.quant_core.protocol import CANONICAL, METHODOLOGY_VERSION, CanonicalProtocol
from bot.quant_core.status import Result, Status, bad, ok

SEED_REGISTRY_FILENAME = "kalman_seeds.json"
INIT_METHOD = "OLS_LOGA_ON_LOGB_AND_CONST_INIT_WINDOW"
THETA_REPRODUCTION_TOLERANCE = 1e-9   # same closes → same numbers; anything larger means different data
HOUR = pd.Timedelta(hours=1)


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True)
class CausalInit:
    theta0: np.ndarray            # [β₀, α₀]
    init_window_start: pd.Timestamp
    init_window_end: pd.Timestamp  # last candle of the window (inclusive)
    seed_timestamp: pd.Timestamp   # first candle the filter steps = init_window_end + 1 h
    n_init_observed: int


def ols_theta(log_a: np.ndarray, log_b: np.ndarray) -> np.ndarray:
    """Closed-form OLS of log_a on [log_b, 1] over the finite pairs. Raises on
    fewer than 2 points or zero variance in log_b (no hedge ratio exists)."""
    a, b = np.asarray(log_a, float), np.asarray(log_b, float)
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    if len(a) < 2:
        raise ValueError(f"causal init needs >= 2 observed candles in the init window, got {len(a)}")
    bc = b - b.mean()
    sxx = float(bc @ bc)
    if not sxx > 0:
        raise ValueError("log P_B has zero variance in the init window — hedge ratio undefined")
    beta = float(bc @ (a - a.mean())) / sxx
    return np.array([beta, float(a.mean() - beta * b.mean())])


def causal_init(log_a: pd.Series, log_b: pd.Series, protocol: CanonicalProtocol = CANONICAL) -> CausalInit:
    """θ₀ from the first L grid candles of a complete hourly grid; the seed
    candle is the (L+1)-th grid candle. Fails closed if L is unset."""
    L = protocol.require_init_window()
    idx = pd.DatetimeIndex(log_a.index)
    if not idx.equals(pd.DatetimeIndex(log_b.index)):
        raise ValueError("log_a and log_b must share one index")
    if len(idx) <= L:
        raise ValueError(f"need more than {L} grid candles to seed after the init window, got {len(idx)}")
    if len(idx) > 1 and not ((idx[1:L + 1] - idx[:L]) == HOUR).all():
        raise ValueError("init window is not on a complete hourly grid")
    if not (np.isfinite(log_a.iloc[L]) and np.isfinite(log_b.iloc[L])):
        # the filter's first step must be an OBSERVED candle on both legs, so
        # research and live (which can only start on a candle it received)
        # begin from the identical state and z history
        raise ValueError(f"seed candle {idx[L]} is not observed on both legs — no canonical seed")
    win_a, win_b = log_a.iloc[:L].to_numpy(float), log_b.iloc[:L].to_numpy(float)
    theta0 = ols_theta(win_a, win_b)
    n_obs = int((np.isfinite(win_a) & np.isfinite(win_b)).sum())
    return CausalInit(theta0, idx[0], idx[L - 1], idx[L], n_obs)


@dataclass(frozen=True)
class KalmanSeed:
    pair_name: str
    seed_timestamp: str          # ISO UTC open time of the first grid candle the filter steps
    beta0: float
    alpha0: float
    methodology_version: str
    protocol_hash: str
    init_window_start: str       # claude code changed: C6 — θ₀ provenance (inclusive window)
    init_window_end: str
    init_method: str
    n_init_observed: int
    data_first_timestamp: str
    data_last_timestamp: str
    n_grid_candles: int
    n_observed_candles: int
    seed_design: str             # claude code changed: Design A lock — "A_PRE_SELECTION"
    data_version: str            # claude code changed: D-5 — content fingerprint of the consumed closes (QBP-DATAV1)

    @property
    def seed_ts(self) -> pd.Timestamp:
        return _utc(self.seed_timestamp)


def seed_from_research(pair_name: str, init: CausalInit, aligned_index: pd.DatetimeIndex, n_observed: int,
                       protocol: CanonicalProtocol = CANONICAL, *, data_version: str) -> KalmanSeed:   # claude code changed: D-5 data_version required
    idx = pd.DatetimeIndex(aligned_index)
    return KalmanSeed(pair_name, _utc(init.seed_timestamp).isoformat(), float(init.theta0[0]), float(init.theta0[1]),
                      METHODOLOGY_VERSION, protocol.config_hash(),
                      _utc(init.init_window_start).isoformat(), _utc(init.init_window_end).isoformat(), INIT_METHOD,
                      init.n_init_observed, _utc(idx[0]).isoformat(), _utc(idx[-1]).isoformat(), len(idx), int(n_observed),
                      protocol.seed_design, data_version)


def seed_id(seed: KalmanSeed) -> str:   # claude code changed: D-4 — the identity of a seed record (lineage key)
    from bot.quant_core.provenance import config_hash
    return config_hash(asdict(seed))


def write_seed(seed: KalmanSeed, output_dir) -> Path:
    path = Path(output_dir) / SEED_REGISTRY_FILENAME
    registry = {}
    if path.exists():
        registry = json.loads(path.read_text(encoding="utf-8"))
    registry[seed.pair_name] = asdict(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_seed(pair_name: str, registry_dir) -> Optional[KalmanSeed]:
    """None if absent. A record written under an older schema (e.g. the
    pre-C6 seed without init-window fields) also loads as None — it can never
    be validated as causal."""
    path = Path(registry_dir) / SEED_REGISTRY_FILENAME
    if not path.exists():
        return None
    row = json.loads(path.read_text(encoding="utf-8")).get(pair_name)
    names = {f.name for f in fields(KalmanSeed)}
    if not row or set(row) != names:
        return None
    return KalmanSeed(**row)


def validate_seed(seed: Optional[KalmanSeed], *, pair_name: str,
                  protocol: CanonicalProtocol = CANONICAL) -> Result:
    """OK only for a record of THIS pair, THIS methodology version and
    protocol hash, with a causal init window that ends exactly one candle
    before the seed candle and a finite θ₀."""
    if seed is None:
        return bad(Status.SKIP, f"{pair_name}: no canonical (causal) Kalman seed record")
    if seed.pair_name != pair_name:
        return bad(Status.SKIP, f"seed record is for {seed.pair_name}, not {pair_name}")
    if seed.methodology_version != METHODOLOGY_VERSION:
        return bad(Status.SKIP, f"{pair_name}: seed methodology {seed.methodology_version} != {METHODOLOGY_VERSION}")
    if seed.protocol_hash != protocol.config_hash():
        return bad(Status.SKIP, f"{pair_name}: seed protocol hash {seed.protocol_hash} != {protocol.config_hash()}")
    if seed.init_method != INIT_METHOD:
        return bad(Status.SKIP, f"{pair_name}: unknown init method {seed.init_method!r}")
    try:
        start, end, s = _utc(seed.init_window_start), _utc(seed.init_window_end), seed.seed_ts
    except (TypeError, ValueError) as e:
        return bad(Status.SKIP, f"{pair_name}: unparseable seed timestamps ({e})")
    if not (start <= end and s - end == HOUR):
        return bad(Status.SKIP, f"{pair_name}: init window [{start}, {end}] does not end exactly one candle before the seed {s} (non-causal)")
    if seed.seed_design != protocol.seed_design:   # claude code changed: Design A lock
        return bad(Status.SKIP, f"{pair_name}: seed design {seed.seed_design!r} != {protocol.seed_design!r}")
    if protocol.init_window_candles is None or (end - start) / HOUR + 1 != protocol.init_window_candles:   # claude code changed: L lock
        return bad(Status.SKIP, f"{pair_name}: init window length {(end - start) / HOUR + 1:.0f} != protocol L {protocol.init_window_candles}")
    from bot.quant_core.provenance import is_sha256
    if not is_sha256(seed.data_version):   # claude code changed: D-5
        return bad(Status.SKIP, f"{pair_name}: seed has no content data version")
    if not (math.isfinite(seed.beta0) and math.isfinite(seed.alpha0)):
        return bad(Status.SKIP, f"{pair_name}: non-finite θ₀")
    return ok(seed)


def verify_theta_reproduction(seed: KalmanSeed, closes_a: dict, closes_b: dict) -> Result:
    """Live data-version check: recompute θ₀ from the exchange's own closes for
    the init window ({open_ms: close}) and require it to equal the record."""
    start, end = _utc(seed.init_window_start), _utc(seed.init_window_end)
    grid = pd.date_range(start, end, freq="h")
    ms = [int(t.value // 1_000_000) for t in grid]
    la = np.array([math.log(closes_a[m]) if m in closes_a and closes_a[m] > 0 else math.nan for m in ms])
    lb = np.array([math.log(closes_b[m]) if m in closes_b and closes_b[m] > 0 else math.nan for m in ms])
    try:
        theta = ols_theta(la, lb)
    except ValueError as e:
        return bad(Status.SKIP, f"{seed.pair_name}: θ₀ not reproducible from exchange init window ({e})")
    diff = float(np.max(np.abs(theta - np.array([seed.beta0, seed.alpha0]))))
    if not diff <= THETA_REPRODUCTION_TOLERANCE:
        return bad(Status.SKIP, f"{seed.pair_name}: θ₀ from exchange data differs from the seed record by {diff:.3e} "
                                f"(research and exchange data differ — data-version mismatch)")
    return ok(theta)


def validate_design_a(seed: KalmanSeed, close_a: pd.Series, close_b: pd.Series, *, selection_start=None,
                      protocol: CanonicalProtocol = CANONICAL) -> Result:   # claude code changed: Design A lock — new
    """Design A check against the source closes: the record is valid
    (validate_seed), the init window lies entirely before the selection zone
    (selection_start defaults to the seed candle, where Design A puts it), and
    θ₀ is reproduced from the init-window closes ALONE. A θ₀ fitted with any
    later (selection-zone) observation does not reproduce and is rejected."""
    v = validate_seed(seed, pair_name=seed.pair_name if seed else "", protocol=protocol)
    if not v.ok:
        return v
    sel = _utc(selection_start) if selection_start is not None else seed.seed_ts
    if not (_utc(seed.init_window_end) < sel and seed.seed_ts <= sel):
        return bad(Status.SKIP, f"{seed.pair_name}: init window ends {seed.init_window_end}, not before the selection zone {sel}")
    ms = lambda ser: {int(_utc(t).value // 1_000_000): float(v) for t, v in ser.items() if np.isfinite(v)}
    return verify_theta_reproduction(seed, ms(close_a), ms(close_b))
