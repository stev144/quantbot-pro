# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/l1_research.py
# claude code changed: new file — 2026-10-07 Deep Health L1, research infrastructure (DEEP_HEALTH_PLAN_2026-10-07.md
# §3 rows 11-15). Known answers and invariants for fold boundaries, zone sealing, provenance, the decision journal
# and inference-method selection; components whose prerequisite owner decision or calibration is open are
# reported BLOCKED with the precise reason (never skipped, never PASS).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from bot.deep_health.core import BLOCKED, PARTIAL, PASS, RESEARCH, Probe, health_check

# The signed D-8 zone definition (QBP-ZONES-1, signed 2026-10-03). A different hash means the zones changed.
SIGNED_ZONES_HASH = "271d720937c22352aaccd5384af388c52df6452a5a1793fb5fbd9281fe52b96c"
OPEN_36 = ("OPEN-36 signed 2026-10-07 (δ = 1 pt, α = 0.05: ≥ 3,202 testable replicates and one-sided 95 % Wilson "
           "upper bound ≤ 6 %): calibration at the signed criterion has not been completed for this component")


# ── 11. OOS validator ───────────────────────────────────────────────────────────────────────────────────────
def label_leaks(folds, horizon: int) -> bool:
    """True when the last training row's forward label (rows i..i+horizon) reaches a test row."""
    return any((f.train_end_pos - 1) + horizon >= f.test_start_pos for f in folds if not f.skipped)


@health_check("oos_validator", "OOS validator: folds, purge, embargo", area=RESEARCH, level="L1", gates=("G1", "G5"),
              reuses=("test_oos_validator (59, DB TestCase — CI suite)",))
def oos_validator():
    from bot.research import oos_validator as ov
    p = Probe()
    ix = pd.date_range("2024-01-01", periods=2000, freq="h", tz="UTC")
    cfg = ov.WalkForwardConfig(min_train_periods=500, test_periods=200, horizon=24, embargo_periods=12)
    folds = [f for f in ov.build_folds(ix, cfg) if not f.skipped]
    p.true("G1", "folds produced", len(folds) >= 5, expected="≥ 5 folds", observed=len(folds))
    gaps = sorted({f.test_start_pos - f.train_end_pos for f in folds})
    p.eq("G5", "train→test gap = purge 24 + embargo 12 = 36 in every fold", [36], gaps)
    p.eq("G5", "purged rows = horizon (24)", [24], sorted({f.n_purged for f in folds}))
    p.true("G5", "no training label reaches the test window", not label_leaks(folds, 24),
           observed=f"leaks={label_leaks(folds, 24)}")
    ordered = all(a.test_end_pos <= b.test_start_pos for a, b in zip(folds, folds[1:]))
    p.true("G5", "chronological, non-overlapping test windows", ordered)
    leaky = ov.build_folds(ix, ov.WalkForwardConfig(min_train_periods=500, test_periods=200, horizon=24, purge_periods=0))
    p.true("G5", "mutation: purge = 0 with a 24-candle label is detected as leakage", label_leaks(leaky, 24),
           expected="leak detected", observed=f"leaks={label_leaks(leaky, 24)}",
           reason="the leak detector cannot see an un-purged boundary")
    p.raises("G1", "overlapping train/test positions refused", lambda: ov.assert_temporal_disjoint([1, 2, 3], [3, 4]))
    return p.out


@health_check("canonical_walk_forward", "Canonical walk-forward (QBP-WF-CANON-1)", area=RESEARCH, level="L1",
              gates=("G5",))
def canonical_walk_forward():
    p = Probe()
    p.add("G5", "owner fold boundaries", BLOCKED, expected="owner-supplied fold boundaries",
          observed="not supplied", reason="owner decision pending (governance 2026-10-04, step 3): canonical "
                                          "walk-forward needs owner fold boundaries; it cannot run, so it cannot be healthy")
    return p.out


# ── 12. research zones / sealed holdout ─────────────────────────────────────────────────────────────────────
@health_check("research_zones", "Research zones and sealed holdout (D-8)", area=RESEARCH, level="L1",
              gates=("G5",), reuses=("test_research_zones (14)",),
              version=lambda: __import__("bot.quant_core.research_zones", fromlist=["x"]).ZONES_VERSION)
def research_zones():
    from bot.quant_core import research_zones as rz
    p = Probe()
    Z, H = rz.ZONES, pd.Timedelta(hours=1)
    cases = [(Z.S1, rz.SELECTION), (Z.S1 + H, rz.EMBARGO), (Z.V0, rz.VALIDATION), (Z.V1 + H, rz.UNASSIGNED_EXPOSED),
             (Z.F - H, rz.UNASSIGNED_EXPOSED), (Z.F, rz.FORWARD_HOLDOUT)]
    p.eq("G5", "boundary classification S1 / S1+1h / V0 / V1+1h / F−1h / F", [z for _, z in cases],
         [Z.classify(t) for t, _ in cases])
    p.raises("G5", "a single candle at F is refused", lambda: Z.assert_no_sealed_data([Z.F], "deep health probe"),
             rz.HoldoutSealedError)
    try:
        Z.assert_no_sealed_data([Z.F - H], "deep health probe")
        p.add("G5", "the last candle before F is allowed", PASS)
    except rz.HoldoutSealedError as e:
        p.add("G5", "the last candle before F is allowed", "FAIL", observed=str(e)[:120], reason="over-sealing")
    df = pd.DataFrame({"v": [1, 2]}, index=[Z.F - H, Z.F])
    research, sealed = Z.split_sealed(df)
    p.eq("G5", "split_sealed routes the F row to the sealed side", (1, 1), (len(research), len(sealed)))
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "ledger.json"
        p.raises("G5", "holdout evaluation refused 1 h before it is due",
                 lambda: rz.open_holdout_evaluation("deep-health-probe", now=Z.E - H, ledger_path=ledger), rz.HoldoutSealedError,
                 match="not due")
        p.true("G5", "a refused evaluation writes nothing to the ledger", not ledger.exists())
    p.eq("G5", "zones definition matches the signed D-8 hash", SIGNED_ZONES_HASH, Z.zones_hash(),
         reason="the research zones were changed after the owner signed them")
    return p.out


# ── 13. provenance / fingerprints ───────────────────────────────────────────────────────────────────────────
@health_check("provenance", "Provenance and data fingerprints", area=RESEARCH, level="L1", gates=("G1", "G5"),
              reuses=("test_stage2d_provenance (36)", "journal provenance tests"))
def provenance():
    from bot.quant_core import provenance as pv
    p = Probe()
    p.eq("G5", "config hash ignores key order", pv.config_hash({"a": 1, "b": 2}), pv.config_hash({"b": 2, "a": 1}))
    p.true("G5", "config hash changes with a value", pv.config_hash({"a": 1, "b": 2}) != pv.config_hash({"a": 1, "b": 3}))
    idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    base = pd.DataFrame({"close": np.linspace(100.0, 101.0, 48)}, index=idx)
    edit = base.copy()
    edit.iloc[20, 0] += 1e-12
    v1 = pv.data_version({"X": base}, timeframe="1h", columns=["close"]).data_version
    v2 = pv.data_version({"X": base.iloc[::-1]}, timeframe="1h", columns=["close"]).data_version
    v3 = pv.data_version({"X": edit}, timeframe="1h", columns=["close"]).data_version
    v4 = pv.data_version({"X": base.drop(idx[10])}, timeframe="1h", columns=["close"]).data_version
    p.eq("G5", "data version ignores row order", v1, v2)
    p.true("G5", "data version changes on a 1e-12 edit (exact repr)", v1 != v3)
    p.true("G5", "data version changes when a candle is removed", v1 != v4)
    code = pv.CodeState("0" * 40, False, None)
    with tempfile.TemporaryDirectory() as d:
        art = Path(d) / "probe.json"
        art.write_text('{"x": 1}', encoding="utf-8")
        side = pv.write_research_output_provenance(art, kind="DEEP_HEALTH_PROBE", producer="bot.deep_health",
                                                   inputs={"version": "probe"}, code=code)
        prov = pv.read_provenance(art)
        p.true("G1", "provenance sidecar written and read back", side is not None and prov.kind == "DEEP_HEALTH_PROBE",
               observed=prov.kind)
        p.eq("G5", "sidecar records the artifact's sha256", pv.file_sha256(art), prov.artifact_sha256)
        p.true("G5", "a LEGACY record is never accepted as canonical",
               not pv.validate_canonical_provenance(prov, kind="DEEP_HEALTH_PROBE", methodology_version="X",
                                                    protocol_hash="").ok)
    cs = pv.code_state()
    if cs.commit is None:
        p.add("G5", "code commit recorded", PARTIAL, expected="40-hex commit", observed=cs.error,
              reason="git unavailable: runs cannot record their code state")
    else:
        p.true("G5", "code commit recorded", len(cs.commit) == 40, expected="40-hex commit",
               observed=f"{cs.commit[:12]}{' (dirty)' if cs.dirty else ''}")
    return p.out


# ── 14. decision journal ────────────────────────────────────────────────────────────────────────────────────
@health_check("journal", "Decision journal: labels, lifecycle, zone guard", area=RESEARCH, level="L1",
              gates=("G1", "G2", "G4", "G5"), reuses=("test_decision_journal_phase1…phase7 (131)",))
def journal():
    from bot.journal import labeler as lb
    from bot.journal import taxonomy as T
    p = Probe()
    run = lb.simulate_rule_path
    # LONG, fill o₀ = 100, stop 98, target 104 (risk 2, reward 4)
    st, _, out = run([100, 101, 102], [101, 104.5, 103], [99.5, 100.5, 101], [100.5, 104, 102], "LONG", 98.0, 104.0, max_hold=3)
    p.eq("G2", "target touched on bar 1 → exit 104 (TARGET)", ("OK", 1, 104.0, "TARGET"),
         (st, out[0].exit_index, out[0].exit_price, out[0].exit_reason))
    st, _, out = run([100, 101, 102], [105, 103, 103], [97, 100, 101], [100, 102, 102], "LONG", 98.0, 104.0, max_hold=3)
    p.eq("G4", "stop and target in the fill bar → stop-first baseline + target-first bound",
         ("STOP", "TARGET", "FILL_BAR_BOTH_TOUCHED"), tuple([o.exit_reason for o in out] + [out[0].ambiguity]))
    st, _, out = run([100, 97, 96], [101, 98, 97], [99, 96, 95], [100, 97, 96], "LONG", 98.0, 104.0, max_hold=3)
    p.eq("G4", "gap through the stop → filled at the open (97), not at the stop", ("GAP_STOP", 97.0),
         (out[0].exit_reason, out[0].exit_price))
    st, _, out = run([100, 100.5, 101], [101, 101, 102], [99, 99.5, 100], [100.5, 101, 101.5], "LONG", 98.0, 104.0, max_hold=3)
    p.eq("G4", "nothing hit within H → time exit at close H−1", ("TIME", 101.5), (out[0].exit_reason, out[0].exit_price))
    st, _, out = run([100, 100.5], [101, 101], [99, 99.5], [100.5, 101], "LONG", 98.0, 104.0, max_hold=3)
    p.eq("G4", "fewer bars than H → CENSORED, no outcome invented", ("CENSORED", 0), (st, len(out)))
    a = run([100, 101, 102, 50], [101, 104.5, 103, 200], [99.5, 100.5, 101, 1], [100.5, 104, 102, 60], "LONG", 98.0, 104.0, max_hold=4)
    b = run([100, 101, 102, 150], [101, 104.5, 103, 300], [99.5, 100.5, 101, 90], [100.5, 104, 102, 250], "LONG", 98.0, 104.0, max_hold=4)
    p.eq("G5", "mutation: bars after the exit do not change the outcome", a[2][0], b[2][0])
    p.raises("G1", "lifecycle out of order refused",
             lambda: T.validate_terminal(["EVALUATED", "GATES_EVALUATED"], "REJECTED", None, None), ValueError)
    p.raises("G1", "EXECUTED without an execution attempt refused",
             lambda: T.validate_terminal(["EVALUATED"], "EXECUTED", None, None), ValueError)
    try:
        T.validate_terminal(["EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED", "EXECUTION_ATTEMPTED"], "EXECUTED", None, None)
        p.add("G1", "a valid executed lifecycle is accepted", PASS)
    except ValueError as e:
        p.add("G1", "a valid executed lifecycle is accepted", "FAIL", observed=str(e))
    p.eq("G1", "live gate order starts with the price-sanity gate", T.G_PRICE, T.LIVE_GATE_ORDER[0])
    from bot.views.decision_journal_data import VISIBLE_OUTCOME_ZONES
    p.eq("G5", "outcomes shown for zone S only", ("SELECTION",), tuple(VISIBLE_OUTCOME_ZONES))
    p.raises("G5", "live/dry-run runs are never labelled (sealed, OD-J4)",
             lambda: lb.label_run(SimpleNamespace(status="CLOSED", environment="DRY_RUN", run_id="probe")), lb.LabelerRefused)
    return p.out


# ── 15. statistical inference (G3) ──────────────────────────────────────────────────────────────────────────
@health_check("inference", "Inference method selection (D-2 / D-10)", area=RESEARCH, level="L1", gates=("G3",),
              reuses=("test_d2_inference (10)", "test_quant_core_calibration (14)"),
              version=lambda: __import__("bot.quant_core.inference", fromlist=["x"]).INFERENCE_METHOD_VERSION)
def inference():
    from bot.quant_core import inference as inf
    p = Probe()
    p.eq("G3", "block length = max(4h, 24) for h = 1 / 6 / 12", (24, 24, 48),
         (inf.d10_block_length(1), inf.d10_block_length(6), inf.d10_block_length(12)))
    p.eq("G3", "confirmatory test = block permutation, B = 999", ("BLOCK_PERMUTATION_D2", 999),
         (inf.CONFIRMATORY_BASIS, inf.CONFIRMATORY_B))
    rng = np.random.default_rng(3)
    x = rng.normal(size=200)
    perfect = inf.confirmatory_ic_test(x, x, 1, seed=1, n_perm=99)
    p.eq("G3", "x = y → p at its floor 1/(B+1) = 0.01 (never 0)", 0.01, perfect["p_value"])
    y = rng.normal(size=200)
    r1, r2 = inf.confirmatory_ic_test(x, y, 1, seed=7, n_perm=99), inf.confirmatory_ic_test(x, y, 1, seed=7, n_perm=99)
    p.eq("G3", "seeded result is reproducible", r1["p_value"], r2["p_value"])
    small = inf.confirmatory_ic_test(x[:30], y[:30], 1, seed=1, n_perm=99)
    p.eq("G3", "fewer than 2 blocks → UNTESTABLE, no substitute p", ("UNTESTABLE", None), (small["status"], small["p_value"]))
    p.add("G3", "formal size calibration gate", BLOCKED, expected="calibrated size within owner δ at owner α",
          observed="recorded calibration (governance 2026-10-03): size 3.3–6.3 % over 9 scenarios", reason=OPEN_36)
    return p.out


@health_check("cointegration_eg", "Engle-Granger cointegration test size", area=RESEARCH, level="L1", gates=("G3",),
              reuses=("test_quant_core_calibration (14)",))
def cointegration_eg():
    p = Probe()
    p.add("G3", "recorded size at 5 %", PARTIAL, expected="≤ 5 % nominal",
          observed="worst Wilson upper bound 13.8 % (heavy tails / GARCH; governance 2026-10-07)",
          reason="descriptive size checks only: modest over-rejection under heavy tails and volatility clustering")
    p.add("G3", "formal size calibration gate", BLOCKED, reason=OPEN_36)
    return p.out


@health_check("step17_null", "Pairs Step-17 null (D-4)", area=RESEARCH, level="L1", gates=("G3",))
def step17_null():
    p = Probe()
    p.add("G3", "size/power calibration (R ≥ 500)", BLOCKED, expected="calibrated before use",
          observed="implemented (QBP-STEP17-D4-1); signed size calibration not run (timing pilot only)",
          reason="owner compute decision pending: the signed size calibration (3,202 replicates × B = 999 full canonical "
                 "runs ≈ 3.2 M evaluations, ≈ 65 days on this PC) and the unspecified D-4 power criterion — the null "
                 "is not usable until both calibrations pass")
    return p.out


@health_check("pit_universe", "Point-in-time universe (D-6)", area=RESEARCH, level="L1", gates=("G5",))
def pit_universe():
    p = Probe()
    p.add("G5", "volume floor and listing table", BLOCKED, expected="owner-defined",
          observed="membership rules implemented (QBP-PIT-D6-1)",
          reason="owner decision D-6 pending: volume floor and listing table — cross-sectional research stays BLOCKED")
    return p.out


@health_check("fx_profile", "Forex market profile (OD-FX-1…9)", area=RESEARCH, level="L1", gates=("G4", "G5"))
def fx_profile():
    # claude code changed: 2026-10-21 — was a hard-coded BLOCKED ("every canonical FX consumer returns
    # FX_CALENDAR_UNAVAILABLE", stale since the owner decisions of 2026-10-16, commit 610e536). Now verified.
    from bot.quant_core.market_profile import AssetClassMismatch, FOREX_PROFILE
    from bot.quant_core.research_zones import ZONES
    from bot.research import dca_engine as de
    from bot.research.entry_exit_engine import EntryExitEngine
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    p = Probe()
    p.eq("G4", "FOREX_PROFILE owner decisions complete", [], FOREX_PROFILE.missing_decisions())
    # canonical Kalman on the FX trading grid: seed = first trading candle at/after S, weekends are not gaps
    cal = FOREX_PROFILE.calendar
    grid = cal.trading_grid(ZONES.init_window()[0] - pd.Timedelta(days=120), ZONES.S0 + pd.Timedelta(days=14))
    rng = np.random.default_rng(7)
    b = pd.Series(1.30 * np.exp(np.cumsum(rng.normal(0, 4e-4, len(grid)))), index=grid)
    a = pd.Series(1.10 * np.exp(0.8 * np.log(b / 1.30) + rng.normal(0, 2e-4, len(grid))), index=grid)
    eng = KalmanFilterEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD", ols_beta=1.0, ols_alpha=0.0)
    res = eng.run_canonical(a, b, output_dir=tempfile.mkdtemp())
    p.eq("G5", "FX canonical seed = first TRADING candle at/after S", str(cal.first_trading_at_or_after(ZONES.S0)),
         str(pd.Timestamp(eng.seed_record.seed_timestamp)))
    p.eq("G4", "FX weekend closures are not Kalman gaps (re-seeds)", 0, int(res["kalman_segment"].max()))
    p.raises("G4", "crypto DCA engine refuses FX (24/7 clock)",
             lambda: de.build_config_with_real_costs(de.BASELINE_CONFIG, "FOREX", symbol="EUR/USD"), AssetClassMismatch, match="24/7 clock")
    p.raises("G4", "canonical pairs lifecycle refuses FX (no swap financing)",
             lambda: EntryExitEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD",
                                     output_dir=tempfile.gettempdir(), rule_set="CANONICAL", asset_class="FOREX"),
             AssetClassMismatch, match="OD-FX-FS-1")
    p.add("G4", "FX swap financing in the canonical pairs ladder", BLOCKED,
          expected="OD-FX-FS-1 (MT5 broker swap, decided 2026-10-16) implemented", observed="not implemented — canonical FX pairs runs refused",
          reason="implementation gap, not an owner decision: canonical FX pairs evidence stays BLOCKED until swap financing exists")
    return p.out
