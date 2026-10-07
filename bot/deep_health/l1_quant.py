# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/l1_quant.py
# claude code changed: new file — 2026-10-07 Deep Health L1, quant engine (DEEP_HEALTH_PLAN_2026-10-07.md §3 rows
# 3-10). Small hand-derived known answers (derivations in the comments, independent of the code under test) and
# invariants. Every call goes through the module attribute (e.g. simulation.apply_slippage) so a corrupted
# function is what gets measured. Nothing here touches an exchange, an order, a position or a database row.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import math

import numpy as np
import pandas as pd

from bot.deep_health.core import BLOCKED, PARTIAL, PASS, QUANT, Probe, health_check

FP = 1e-9     # tolerance for floating-point identities of a few additions/multiplications on O(1e3) values
UTC = "UTC"


# ── 3. position sizer ───────────────────────────────────────────────────────────────────────────────────────
@health_check("position_sizer", "Risk / position sizer", area=QUANT, level="L1", gates=("G1", "G2", "G4"),
              reuses=("test_fixed_fraction_execution", "test_risk_controls_inheritance", "deep_health_check L3 _test_q_*"))
def position_sizer():
    from bot.config import risk
    from bot.risk import position_sizer as ps
    p = Probe()
    s = ps.PositionSizer(risk_pct=0.01)
    # 1 % of 10,000 = 100 at risk; |100 − 98| = 2 per unit → 50 units
    q = s.calculate(10_000, 100.0, 98.0)
    p.eq("G2", "long: 10,000 × 1 % / |100 − 98| = 50", 50.0, q)
    p.eq("G2", "short: stop above entry is symmetric (|100 − 102| → 50)", 50.0, s.calculate(10_000, 100.0, 102.0))
    p.near("G4", "risk taken = 1 % of balance", 100.0, q * 2.0, FP, "fp identity")
    # 100 / 3 = 33.333…; the sizer floors to 4 dp, never rounds up (rounding up would risk more than allowed)
    p.eq("G4", "floors to 4 dp, never rounds up: 100/3 → 33.3333", 33.3333, s.calculate(10_000, 100.0, 97.0))
    big = ps.PositionSizer(risk_pct=0.10)
    qb = big.calculate(10_000, 100.0, 98.0)
    p.eq("G4", f"a 10 % request is capped at MAX_RISK_PCT = {risk.MAX_RISK_PCT:.0%} (→ 250)", 250.0, qb)
    p.true("G4", "risk never exceeds the cap", qb * 2.0 <= 10_000 * risk.MAX_RISK_PCT + FP,
           expected=f"≤ {10_000 * risk.MAX_RISK_PCT}", observed=qb * 2.0)
    p.eq("G1", "entry == stop → 0 (skip trade, fail-closed)", 0.0, s.calculate(10_000, 100.0, 100.0))
    p.eq("G1", "balance 0 → 0 (skip trade, fail-closed)", 0.0, s.calculate(0, 100.0, 98.0))
    p.eq("G1", "negative stop → 0 (skip trade, fail-closed)", 0.0, s.calculate(10_000, 100.0, -1.0))
    return p.out


# ── 4. canonical pair P&L ───────────────────────────────────────────────────────────────────────────────────
@health_check("pair_pnl", "Canonical pair P&L (bot.pairs.pnl)", area=QUANT, level="L1", gates=("G1", "G2", "G4"),
              reuses=("test_pairs_pnl_component (23)", "test_pairs_pnl_identity (7)"))
def pair_pnl():
    from bot.pairs import pnl
    p = Probe()
    t0 = pd.Timestamp("2024-01-01", tz=UTC)
    costs = pnl.PairCosts(fee_rate=0.001, slippage_rate=0.0005, borrow_rate_hourly_a=0.0, borrow_rate_hourly_b=0.0001)
    # LONG_SPREAD, β = 2, T = 3000, M_A = 100, M_B = 50: N_A = 3000/3 = 1000 → q_A = +10, q_B = −2·1000/50 = −40
    pos = pnl.open_pair_position("LONG_SPREAD", 2.0, 3000.0, 100.0, 50.0, t0)
    p.eq("G2", "q_A = d·N_A/M_A = +10", 10.0, pos.qty_a)
    p.eq("G2", "q_B = −d·β·N_A/M_B = −40", -40.0, pos.qty_b)
    # exit 10 h later at 110 / 52: mark = 10·10 + (−40)·2 = 20
    # slippage = 10·0.0005·(100+110) + 40·0.0005·(50+52) = 1.05 + 2.04 = 3.09
    # fills: A long 100.05 → 109.945; B short 49.975 → 52.026; fees = 0.001·(10·209.995 + 40·102.001) = 6.17999
    # borrow (short leg B only) = 40 · 0.0001 · 10 h · 52.026 = 2.08104; net = 20 − 3.09 − 6.17999 − 2.08104 = 8.64897
    sl, rest = pnl.close_pair_slice(pos, 1.0, 110.0, 52.0, t0 + pd.Timedelta(hours=10), costs)
    p.near("G2", "mark P&L = 20", 20.0, sl.mark_pnl, FP, "fp identity")
    p.near("G2", "slippage = 3.09", 3.09, sl.slippage_cost, FP, "fp identity")
    p.near("G2", "fees = 6.17999", 6.17999, sl.fee_cost, FP, "fp identity")
    p.near("G4", "borrow on the short leg = 2.08104", 2.08104, sl.borrow_cost, FP, "fp identity")
    p.near("G2", "net = 8.64897", 8.64897, sl.net_pnl, FP, "fp identity")
    p.near("G2", "identity: net = mark − slippage − fees − borrow", sl.mark_pnl - sl.slippage_cost - sl.fee_cost - sl.borrow_cost,
           sl.net_pnl, FP, "fp identity")
    p.true("G1", "full close leaves no remaining position", rest is None, observed=rest)
    short = pnl.open_pair_position("SHORT_SPREAD", 2.0, 3000.0, 100.0, 50.0, t0)
    ss, _ = pnl.close_pair_slice(short, 1.0, 110.0, 52.0, t0 + pd.Timedelta(hours=10), costs)
    p.near("G2", "SHORT_SPREAD mirrors the sign: mark P&L = −20", -20.0, ss.mark_pnl, FP, "fp identity")
    h1, rem = pnl.close_pair_slice(pos, 0.5, 110.0, 52.0, t0 + pd.Timedelta(hours=10), costs)
    h2, _ = pnl.close_pair_slice(rem, 1.0, 110.0, 52.0, t0 + pd.Timedelta(hours=10), costs)
    p.near("G2", "half + half = whole (net)", sl.net_pnl, h1.net_pnl + h2.net_pnl, FP, "fp identity")
    # 30 minutes held: the first hour is charged → 40 · 0.0001 · 1 · 52.026 = 0.208104
    early, _ = pnl.close_pair_slice(pos, 1.0, 110.0, 52.0, t0 + pd.Timedelta(minutes=30), costs)
    p.near("G4", "first (started) hour of borrow is charged", 0.208104, early.borrow_cost, FP, "fp identity")
    long_only = pnl.PairCosts(0.001, 0.0005, borrow_rate_hourly_a=0.01, borrow_rate_hourly_b=0.0)
    lo, _ = pnl.close_pair_slice(pos, 1.0, 110.0, 52.0, t0 + pd.Timedelta(hours=10), long_only)
    p.eq("G4", "no borrow is charged on the long leg", 0.0, lo.borrow_cost)
    p.raises("G1", "negative cost rate refused", lambda: pnl.PairCosts(-0.001, 0.0), ValueError)
    p.raises("G1", "unknown direction refused", lambda: pnl.open_pair_position("UP", 1.0, 100.0, 1.0, 1.0, t0), ValueError)
    return p.out


# ── 5. execution timing contract ────────────────────────────────────────────────────────────────────────────
@health_check("timing_contract", "Execution timing contract (signal at close t, fill at open t+1)", area=QUANT,
              level="L1", gates=("G1", "G4"), reuses=("test_timing_contract (4)",))
def timing_contract():
    from bot.quant_core import timing
    p = Probe()
    c = timing.RESEARCH_LABEL_CONTRACT
    p.eq("G4", "canonical contract: delay 1 bar, fill at the open", (1, "open"), (c.execution_delay_bars, c.execution_price))
    df = pd.DataFrame({"open": [100.0, 101.0, 103.0, 106.0, 110.0], "close": [100.5, 102.0, 105.0, 109.0, 111.0]})
    lab = timing.forward_label(df, 1, c)
    # decided at close 0 → filled at open 1 (101) → exited at open 2 (103): 103/101 − 1
    p.near("G4", "label₀ = open₂/open₁ − 1 = 103/101 − 1", 103.0 / 101.0 - 1.0, lab.iloc[0], 1e-15, "fp division")
    p.near("G4", "label₂ = 110/106 − 1", 110.0 / 106.0 - 1.0, lab.iloc[2], 1e-15, "fp division")
    p.true("G4", "the last d + h labels are censored (NaN), never filled", bool(lab.iloc[3:].isna().all()),
           observed=list(lab.iloc[3:]))
    p.raises("G4", "d = 0 (fill at the signal print) refused", lambda: timing.TimingContract(execution_delay_bars=0),
             ValueError, match="F-04")
    p.raises("G1", "no open column → refused, no fallback to close",
             lambda: timing.research_forward_return(df[["close"]], 1), ValueError, match="no fallback")
    return p.out


# ── 6. execution costs / slippage / fees ────────────────────────────────────────────────────────────────────
@health_check("execution_costs", "Execution costs, slippage, fees and sign conventions", area=QUANT, level="L1",
              gates=("G1", "G2", "G4"),
              reuses=("test_execution_costs (3)", "test_cost_model (10)", "test_order_manager_cost_overrides"))
def execution_costs():
    from bot.config import execution_costs as ec
    from bot.engines import simulation as sim
    p = Probe()
    r = 0.0005
    p.eq("G4", "LONG entry pays up: 100 × (1 + 0.0005)", 100.05, sim.apply_slippage(100.0, "LONG", True, r))
    p.eq("G4", "LONG exit sells down: 100 × (1 − 0.0005)", 99.95, sim.apply_slippage(100.0, "LONG", False, r))
    p.eq("G4", "SHORT entry sells down", 99.95, sim.apply_slippage(100.0, "SHORT", True, r))
    p.eq("G4", "SHORT exit pays up", 100.05, sim.apply_slippage(100.0, "SHORT", False, r))
    p.eq("G2", "fees: (100 + 110) × 2 × 0.1 % = 0.42", 0.42, sim.calc_fees(100.0, 110.0, 2.0, 0.001))
    p.eq("G2", "gross P&L LONG 100 → 110 × 2 = +20", 20.0, sim.calculate_pnl("LONG", 100.0, 110.0, 2.0))
    p.eq("G2", "gross P&L SHORT 100 → 110 × 2 = −20", -20.0, sim.calculate_pnl("SHORT", 100.0, 110.0, 2.0))
    kr, bn = ec.get_venue_execution_costs("kraken"), ec.get_venue_execution_costs("binance")
    p.eq("G4", "Kraken fee = 0.26 % (lowest published tier)", 0.0026, kr["fee_rate"])
    p.eq("G4", "single-source FEE_RATE = the Binance row", ec.FEE_RATE, bn["fee_rate"])
    # Existing fallbacks, REPORTED (not changed — execution behaviour is out of scope for this upgrade):
    odd = sim.apply_slippage(100.0, "SIDEWAYS", True, r)
    p.add("G1", "unknown direction → refusal", PASS if odd != 100.0 else PARTIAL, expected="a refusal (exception)",
          observed=f"returned {odd} unchanged with a logged warning",
          reason="" if odd != 100.0 else "silent fallback: an unknown direction yields a usable-looking price (existing behaviour, reported not changed)")
    bad = sim.calculate_pnl("LONG", -1.0, 110.0, 2.0)
    p.add("G1", "invalid P&L input → refusal", PASS if bad != 0.0 else PARTIAL, expected="a refusal (exception)",
          observed=f"returned {bad}", reason="" if bad != 0.0 else
          "silent fallback: invalid inputs return 0.0, indistinguishable from a flat trade (existing behaviour, reported not changed)")
    unk = ec.get_venue_execution_costs("no-such-venue")
    p.add("G1", "unknown venue → refusal", PARTIAL if unk == bn else PASS, expected="a refusal (KeyError)",
          observed=f"returned {unk}", reason="silent fallback: an unknown venue silently receives Binance's costs "
                                              "(documented design, reported not changed)" if unk == bn else "")
    return p.out


# ── 7. funding / financing ──────────────────────────────────────────────────────────────────────────────────
def _funding_fixture(rates):
    from bot.quant_core.funding import normalise_funding
    idx = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00", "2024-01-02 00:00"], utc=True)
    px = pd.Series(100.0, index=pd.date_range("2023-12-31 23:00", "2024-01-02 01:00", freq="h", tz=UTC))
    return normalise_funding(pd.Series(rates, index=idx)), px


@health_check("funding", "Funding sign and short financing (quant_core.funding)", area=QUANT, level="L1",
              gates=("G1", "G2", "G4"), reuses=("test_perp_funding (10)",))
def funding():
    from bot.quant_core import funding as fu
    p = Probe()
    f, px = _funding_fixture([0.0001, 0.0003, 0.0001, 0.0002])
    # 2 units short at 100 = 200 notional; settlements inside (00:00, 16:00] = 08:00 (+0.0003) and 16:00 (+0.0001)
    # positive funding is PAID BY LONGS TO SHORTS → the short's cost is −(0.0004 × 200) = −0.08 (income)
    fc = fu.funding_cost(2, px, f, "2024-01-01 00:00", "2024-01-01 16:00")
    p.near("G2", "cost = −Σ rate × notional = −0.08", -0.08, fc.cost, FP, "fp identity")
    p.eq("G4", "settlement at t0 excluded, t1 included (2 settlements)", 2, fc.n_settlements)
    neg, _ = _funding_fixture([-0.0001, -0.0003, -0.0001, -0.0002])
    fn = fu.funding_cost(2, px, neg, "2024-01-01 00:00", "2024-01-01 16:00")
    p.near("G4", "negative funding: the short pays (+0.08)", 0.08, fn.cost, FP, "fp identity")
    out = fu.funding_cost(2, px, f, "2024-01-01 00:00", "2024-01-03 00:00")
    p.true("G4", "window beyond the funding history → unavailable, never 0",
           out.cost is None and out.complete is False, expected="cost None, complete False", observed=(out.cost, out.complete))
    gap = fu.normalise_funding(pd.Series([0.0001, 0.0001], index=pd.to_datetime(["2024-01-01 00:00", "2024-01-01 16:00"], utc=True)))
    g = fu.funding_cost(2, px, gap, "2024-01-01 00:00", "2024-01-01 16:00")
    p.true("G4", "a gap > 8 h between settlements → unavailable", g.cost is None, expected="cost None", observed=g.cost)
    p.raises("G1", "NaN rates refused, never imputed",
             lambda: fu.normalise_funding(pd.Series([0.0001, float("nan")], index=pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True))),
             ValueError)
    return p.out


# ── 8. regime detector (L1 behaviour; causal mutation is L2) ────────────────────────────────────────────────
def regime_frame(closes):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2023-01-01", periods=len(c), freq="h", tz=UTC)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c,
                         "volume": 1000.0}, index=idx)


def regime_fixtures():
    rng = np.random.default_rng(20261007)
    up = 100 * np.exp(np.cumsum(0.004 + rng.normal(0, 0.002, 400)))
    flat = 100 + rng.normal(0, 0.05, 400)
    return up, flat


@health_check("regime_detector", "Regime detector (behaviour)", area=QUANT, level="L1", gates=("G1", "G2"),
              reuses=("test_regime_precomputer (1)", "deep_health_check L4 pipeline"))
def regime_detector():
    from bot.engines import regime_detector as rd
    p = Probe()
    up, flat = regime_fixtures()
    det = rd.RegimeDetector()
    r_up = det.detect(regime_frame(up))
    p.eq("G2", "steady +0.4 %/h synthetic up-trend → TRENDING_UP", "TRENDING_UP", r_up.regime)
    r_flat = det.detect(regime_frame(flat))
    p.eq("G2", "flat low-volatility synthetic series → RANGING", "RANGING", r_flat.regime)
    fields = ("regime", "confidence", "adx", "atr_ratio", "ema_spread_pct", "bb_width_pct", "adx_trending", "volatility_extreme")
    missing = [f for f in fields if getattr(r_up, f, None) is None]
    p.eq("G1", "every RegimeResult field populated", [], missing)
    p.true("G2", "ADX higher in the trend than in the flat series", r_up.adx > r_flat.adx,
           expected="adx(up) > adx(flat)", observed=f"{r_up.adx:.2f} vs {r_flat.adx:.2f}")
    return p.out


# ── 9. canonical Kalman (L1; future-mutation causality is L2) ───────────────────────────────────────────────
def kalman_fixture(n=1500, beta=1.5, alpha=0.2):
    rng = np.random.default_rng(15)
    lb = np.cumsum(rng.normal(0, 0.01, n)) + 3.0
    la = alpha + beta * lb + rng.normal(0, 0.03, n)
    return la, lb


@health_check("kalman", "Canonical Kalman filter (quant_core.kalman)", area=QUANT, level="L1", gates=("G1", "G2", "G5"),
              reuses=("test_quant_core_kalman (17)", "test_kalman_seed_lineage (8)"),
              version=lambda: __import__("bot.quant_core.protocol", fromlist=["x"]).METHODOLOGY_VERSION)
def kalman():
    from bot.quant_core import kalman as km
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.research_zones import ZONES
    p = Probe()
    la, lb = kalman_fixture()
    k = km.CanonicalKalman(np.array([1.0, 0.0]))          # deliberately wrong seed β₀ = 1.0
    for a, b in zip(la, lb):
        k.step(a, b)
    sd = float(math.sqrt(k.P[0, 0]))
    p.near("G2", "recovers the true β = 1.5 from a wrong seed", 1.5, float(k.theta[0]), 4 * sd,
           f"4 × the filter's own posterior sd ({sd:.4f})")
    k2 = km.CanonicalKalman(np.array([1.5, 0.2]))
    k2.step(la[0], lb[0])
    before = k2.theta.copy()
    miss = k2.step(float("nan"), lb[1])
    p.true("G1", "a missing candle is prediction-only (θ unchanged)", (not miss.observed) and np.array_equal(before, k2.theta),
           observed=f"observed={miss.observed}, θ changed={not np.array_equal(before, k2.theta)}")
    for _ in range(CANONICAL.gap_max_candles + 1):
        k2.step(float("nan"), float("nan"))
    p.eq("G5", f"a gap > {CANONICAL.gap_max_candles} candles invalidates the filter", False, k2.valid)
    res = k2.step(la[10], lb[10])
    p.true("G5", "the next observation re-seeds causally to θ₀ (segment + 1)",
           res.reseeded and res.segment == 1 and np.array_equal(res.theta_prior, k2.theta0),
           expected="reseeded, segment 1, prior = θ₀", observed=f"reseeded={res.reseeded}, segment={res.segment}")
    p.eq("G5", "canonical seed candle = the selection-zone start (D-8)", ZONES.S0, CANONICAL.require_selection_start())
    return p.out


# ── 10. DCA engine ──────────────────────────────────────────────────────────────────────────────────────────
def dca_fixture():
    idx = pd.date_range("2024-01-01", periods=5, freq="h", tz=UTC)
    o = [100.0, 100.0, 99.0, 88.0, 90.0]
    c = [100.0, 100.0, 89.0, 90.0, 91.0]
    return pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c), "low": np.minimum(o, c)}, index=idx)


@health_check("dca", "DCA engine (research.dca_engine)", area=QUANT, level="L1", gates=("G1", "G2", "G3", "G4", "G5"),
              reuses=("test_dca_engine (27)", "test_dca_phase2 (41)"))
def dca():
    from bot.quant_core.research_zones import HoldoutSealedError, ZONES
    from bot.research import dca_engine as de
    p = Probe()
    cfg = de.DCAConfig(initial_usdt=100.0, levels=(de.DCALevel(0.10, 2.0),), fee_rate=0.0, slippage_rate=0.0)
    df = dca_fixture()
    trades, _ = de.simulate_dca_path(df, cfg)
    p.eq("G1", "one cycle", 1, len(trades))
    t = trades[0]
    fills = t["cycle_fills"]
    # decided at close 0 (no cycle) → opened at OPEN 1 = 100
    p.eq("G4", "first entry fills at the next bar's open (bar 1, price 100)", (df.index[1], 100.0),
         (fills[0]["timestamp"], fills[0]["price"]))
    # close 2 = 89 ≤ 100 × (1 − 10 %) = 90 → level confirmed at that close → filled at OPEN 3 = 88 (not at 89)
    p.eq("G4", "ladder level fills at the open after the confirming close (bar 3, price 88)", (df.index[3], 88.0),
         (fills[1]["timestamp"], fills[1]["price"]))
    # Σ usdt = 100 + 200 = 300; Σ qty = 1 + 200/88; weighted average = 300 / (1 + 200/88) = 91.6666…
    p.near("G2", "weighted average = Σ usdt / Σ qty = 91.6667", 300.0 / (1.0 + 200.0 / 88.0), t["entry_price"], 1e-6,
           "the engine rounds prices to 8 dp")
    p.eq("G2", "capital deployed = Σ usdt = 300", 300.0, t["cycle_max_capital_deployed"])
    p.true("G4", "open at data end → censored, labelled mark, no fabricated fill",
           t["censored"] is True and t["cycle_exit_reason"] == "DATA_END_STILL_OPEN" and t["cycle_n_fills"] == 2,
           expected="censored, DATA_END_STILL_OPEN, 2 fills",
           observed=(t["censored"], t["cycle_exit_reason"], t["cycle_n_fills"]))
    sealed = df.copy()
    sealed.index = pd.date_range(ZONES.F, periods=5, freq="h")
    p.raises("G5", "sealed holdout candles refused", lambda: de.simulate_dca_path(sealed, cfg), HoldoutSealedError)
    p.add("G3", "D-5 DCA statistic calibration", BLOCKED, expected="calibrated power gate (QBP-DCA-D5-1)",
          observed="statistic implemented; calibration not run",
          reason="owner decision D-5 pending: calibration needs owner definitions — DCA stays descriptive only")
    return p.out
