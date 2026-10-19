# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/l2_integrity.py
# claude code changed: new file — 2026-10-07 Deep Health L2 (DEEP_HEALTH_PLAN_2026-10-07.md §3 rows 8, 9, 15, 16).
# On demand only (`python manage.py deep_health_check --level L2`), never from a page load:
#   * causal mutation checks — changing future observations must not change earlier outputs;
#   * descriptive calibration runs (null size, interval coverage) — reported with their numbers, gate BLOCKED
#     until the owner declares the calibration margins (OPEN-36);
#   * the canonical DB-free test suites (SimpleTestCase — Django forbids database queries in them) re-run
#     in-process, unchanged, as the deepest evidence for each component.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import contextlib
import io
import logging
import unittest

import numpy as np
import pandas as pd

from bot.deep_health.core import BLOCKED, FAIL, PASS, QUANT, RESEARCH, UNKNOWN, Probe, health_check
from bot.deep_health.l1_quant import kalman_fixture, regime_fixtures, regime_frame
from bot.deep_health.l1_research import OPEN_36

REGIME_FIELDS = ("regime", "confidence", "adx", "atr_ratio", "ema_spread_pct", "bb_width_pct", "adx_trending",
                 "volatility_extreme")


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (np.isnan(a) and np.isnan(b)) or a == b
    return a == b


# ── 8. regime detector causality ────────────────────────────────────────────────────────────────────────────
@health_check("regime_causal", "Regime detector causality (look-ahead mutation)", area=QUANT, level="L2", gates=("G5",),
              reuses=("test_regime_precomputer (1)",))
def regime_causal():
    from bot.backtesting import regime_precomputer as rp
    from bot.engines.regime_detector import RegimeDetector
    p = Probe()
    up, flat = regime_fixtures()
    df = regime_frame(np.r_[up, flat * up[-1] / 100.0])
    det = RegimeDetector()
    full = rp.precompute_regime_results(df, det)
    rng = np.random.default_rng(99)
    for k in (200, 450, 700):
        mut = df.copy()
        noise = np.exp(rng.normal(0, 0.05, len(df) - k))
        for col in ("open", "high", "low", "close"):
            mut.iloc[k:, mut.columns.get_loc(col)] = mut[col].to_numpy()[k:] * noise
        mutated = rp.precompute_regime_results(mut, det)
        diffs = [i for i in range(k) if not all(_same(getattr(full[i], f), getattr(mutated[i], f)) for f in REGIME_FIELDS)]
        p.eq("G5", f"rows < {k} unchanged when rows ≥ {k} are replaced", [], diffs[:5],
             reason="look-ahead: earlier regime outputs depend on later candles")
    mism = [i for i in (250, 500, 799)
            if not all(_same(getattr(full[i], f), getattr(det.detect(df.iloc[:i + 1]), f)) for f in ("regime", "confidence"))]
    p.eq("G5", "vectorised precompute = per-candle detect on the prefix", [], mism)
    return p.out


# ── 9. Kalman causality ─────────────────────────────────────────────────────────────────────────────────────
@health_check("kalman_causal", "Canonical Kalman causality (future mutation)", area=QUANT, level="L2", gates=("G5",),
              reuses=("test_quant_core_kalman (17)",))
def kalman_causal():
    from bot.quant_core import kalman as km
    p = Probe()
    la, lb = kalman_fixture(n=800)
    la2 = la.copy()
    la2[400:] += np.random.default_rng(5).normal(0, 0.5, 400)

    def path(a):
        k = km.CanonicalKalman(np.array([1.5, 0.2]))
        return np.array([k.step(x, y).theta_post for x, y in zip(a, lb)])
    base, mut = path(la), path(la2)
    p.true("G5", "θ(t) for t < 400 unchanged when observations ≥ 400 change", np.array_equal(base[:400], mut[:400]),
           observed=f"max |Δθ| before 400 = {float(np.abs(base[:400] - mut[:400]).max()):.3g}")
    p.true("G5", "the mutation does change later outputs (the check is sensitive)", not np.array_equal(base[400:], mut[400:]))
    return p.out


# ── 15. descriptive calibration runs (gates BLOCKED by OPEN-36) ─────────────────────────────────────────────
def _ar1(rng, n, phi=0.5):
    e = rng.normal(size=n)
    x = np.empty(n)
    x[0] = e[0]
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


@health_check("inference_size", "Block-permutation null size (descriptive run)", area=RESEARCH, level="L2", gates=("G3",),
              reuses=("test_d2_inference (10)",))
def inference_size():
    from bot.quant_core import calibration as cal
    from bot.quant_core import inference as inf
    p = Probe()
    rng = np.random.default_rng(20261007)
    M, rej = 200, 0
    for m in range(M):
        r = inf.confirmatory_ic_test(_ar1(rng, 240), _ar1(rng, 240), 1, seed=m, n_perm=99)
        rej += int(r["p_value"] is not None and r["p_value"] <= 0.05)
    lo, hi = cal.wilson_bounds(rej, M, 0.95)
    p.add("G3", "null size at 5 % (independent AR(1), φ = 0.5, n = 240, B = 99, 200 reps)", BLOCKED,
          expected="within owner δ of 5 %", observed=f"{rej}/{M} = {rej / M:.1%} (95 % Wilson {lo:.1%}–{hi:.1%})",
          reason=OPEN_36)
    return p.out


@health_check("bootstrap_coverage", "Journal time-block bootstrap coverage (descriptive run)", area=RESEARCH,
              level="L2", gates=("G3",), reuses=("test_decision_journal_phase4",))
def bootstrap_coverage():
    from bot.quant_core import calibration as cal
    from bot.quant_core import inference as inf
    p = Probe()
    rng = np.random.default_rng(11)
    times = pd.date_range("2022-01-01", periods=240, freq="h", tz="UTC")
    M, hit, refused = 200, 0, 0
    for m in range(M):
        r = inf.time_block_bootstrap_mean_ci(rng.normal(0, 1, 240), times, block_hours=24, seed=m, n_boot=199)
        if not r["ok"]:
            refused += 1
            continue
        hit += int(r["lo"] <= 0.0 <= r["hi"])
    n = M - refused
    lo, hi = cal.wilson_bounds(hit, n, 0.95) if n else (float("nan"), float("nan"))
    p.add("G3", "95 % interval coverage of the true mean (iid normal, 10 daily blocks, 200 reps)", BLOCKED,
          expected="within owner δ of 95 %", observed=f"{hit}/{n} = {hit / max(n, 1):.1%} (95 % Wilson {lo:.1%}–{hi:.1%}); refused {refused}",
          reason=OPEN_36)
    return p.out


# ── 16. canonical suites re-run in-process ──────────────────────────────────────────────────────────────────
QUANT_SUITES = (("test_fixed_fraction_execution", "G4"), ("test_pairs_pnl_component", "G2"),
                ("test_pairs_pnl_identity", "G2"), ("test_timing_contract", "G4"), ("test_execution_costs", "G4"),
                ("test_cost_model", "G4"), ("test_perp_funding", "G4"), ("test_quant_core_kalman", "G2"),
                ("test_kalman_seed_lineage", "G5"), ("test_dca_engine", "G4"), ("test_dca_phase2", "G4"))
RESEARCH_SUITES = (("test_research_zones", "G5"), ("test_d2_inference", "G3"), ("test_quant_core_calibration", "G3"),
                   ("test_pca_causal", "G5"), ("test_decision_journal_phase2", "G2"))


def _flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def run_suite(module: str):
    """(n_run, n_skipped_db, failures) — DB-backed TestCase classes are not run here (they need the test DB; they
    run in the CI suite), only SimpleTestCase classes."""
    from django.test import SimpleTestCase, TransactionTestCase
    tests = list(_flatten(unittest.TestLoader().loadTestsFromName(f"bot.tests.{module}")))
    db_free = [t for t in tests if isinstance(t, SimpleTestCase) and not isinstance(t, TransactionTestCase)]
    prev_disable = logging.root.manager.disable          # some suites assert log records: logging must be on
    logging.disable(logging.NOTSET)
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # engines print reports; keep the health output clean
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(unittest.TestSuite(db_free))
    finally:
        logging.disable(prev_disable)
    bad = [str(t).split(" ")[0] for t, _ in result.failures + result.errors]
    return result.testsRun, len(tests) - len(db_free), bad


def _suites_component(suites):
    p = Probe()
    for module, gate in suites:
        n, n_db, bad = run_suite(module)
        if n == 0:
            p.add(gate, module, UNKNOWN, observed=f"0 DB-free tests ({n_db} DB tests run only in CI)",
                  reason="no in-process evidence")
        else:
            p.add(gate, module, PASS if not bad else FAIL, expected="all tests pass",
                  observed=f"{n - len(bad)}/{n} passed" + (f"; {n_db} DB tests in CI only" if n_db else ""),
                  reason="" if not bad else "failing: " + ", ".join(bad[:5]))
    return p.out


@health_check("quant_suites", "Canonical quant test suites (re-run in-process)", area=QUANT, level="L2",
              gates=("G2", "G4", "G5"), reuses=tuple(m for m, _ in QUANT_SUITES))
def quant_suites():
    return _suites_component(QUANT_SUITES)


@health_check("research_suites", "Canonical research test suites (re-run in-process)", area=RESEARCH, level="L2",
              gates=("G2", "G3", "G5"), reuses=tuple(m for m, _ in RESEARCH_SUITES))
def research_suites():
    return _suites_component(RESEARCH_SUITES)
