# ============================================================
# bot/research/parity_forensics.py
# claude code changed: new file — Stage 2D Phase O forensic parity instrument.
#
# MEASUREMENT ONLY. Feeds identical candles to the research path
# (KalmanFilterEngine batch + EntryExitEngine) and the live path
# (bot.pairs.signal_engine.PairSignalEngine, driven exactly as
# bot/core/pairs_bot_runner.py drives it: optional bootstrap of the most recent
# candles, then process_candle per closed candle). Nothing in either engine is
# changed or tuned here.
#
# Every numeric field is compared with an explicit absolute AND relative
# tolerance; max |error| and max relative error are always reported, never just
# "pass". Availability mismatches (one side has a value, the other does not)
# are counted separately.
#
# Live P&L is MODELLED: the live engine's own decisions, β and leg sizes, filled
# at the processed candle's close, priced with the research cost model through
# bot.pairs.pnl. The real live execution layer (ticker fills, exchange fees,
# borrow) is not replayable without an exchange and is reported as such.
# ============================================================

from __future__ import annotations

import contextlib
import io
import logging
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from bot.pairs.config import PairConfig
from bot.pairs.pnl import PairCosts, close_pair_slice, open_pair_position
from bot.pairs.signal_engine import PairSignalEngine
from bot.research.entry_exit_engine import EntryExitEngine
from bot.research.kalman_filter_engine import KalmanFilterEngine

HOUR = pd.Timedelta(hours=1)
FLOAT_ATOL = 1e-10      # declared: floating-point accumulation over a few thousand recursive steps
FLOAT_RTOL = 1e-10


# ── field comparison ─────────────────────────────────────────────────────────

@dataclass
class FieldStat:
    name: str
    category_if_unequal: str
    n_both: int
    only_research: int
    only_live: int
    max_abs_error: float
    max_rel_error: float
    atol: float
    rtol: float

    @property
    def equal(self) -> bool:
        if self.n_both == 0:
            return False
        if self.only_research or self.only_live:
            return False
        return self.max_abs_error <= self.atol or self.max_rel_error <= self.rtol


def compare_numeric(name: str, research: pd.Series, live: pd.Series, category: str,
                    atol: float = FLOAT_ATOL, rtol: float = FLOAT_RTOL) -> FieldStat:
    r, l = research.astype(float), live.reindex(research.index).astype(float)
    rf, lf = np.isfinite(r.to_numpy()), np.isfinite(l.to_numpy())
    both = rf & lf
    d = np.abs(r.to_numpy()[both] - l.to_numpy()[both])
    scale = np.maximum(np.abs(r.to_numpy()[both]), 1e-300)
    return FieldStat(
        name, category, int(both.sum()), int((rf & ~lf).sum()), int((lf & ~rf).sum()),
        float(d.max()) if d.size else math.nan, float((d / scale).max()) if d.size else math.nan, atol, rtol,
    )


# ── the two paths ────────────────────────────────────────────────────────────

def _cfg(hedge_ratio, intercept, win_rate, capital) -> PairConfig:
    return PairConfig("X_USDT/Y_USDT", "X_USDT", "Y_USDT", "X/USDT", "Y/USDT",
                      hedge_ratio, intercept, win_rate, capital, True, "", True)


def research_state(close_a: pd.Series, close_b: pd.Series, hedge_ratio: float, intercept: float) -> pd.DataFrame:
    """Research batch on the candles it is given, through the engine's own
    alignment (Stage 2D Rev B: absent rows stay gaps on the hourly grid)."""
    eng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                             ols_beta=hedge_ratio, ols_alpha=intercept)
    la, lb = eng._to_log_prices(eng._align_prices(close_a, close_b))   # claude code changed: Stage 2D Rev B — was np.log on the given rows, bypassing _align_prices
    b = eng._run_filter(la, lb, eng._initialise_state(la, lb))
    b = eng._calculate_dynamic_spread(b, la, lb)
    b = eng._calculate_dynamic_zscore(b)
    return b


@dataclass
class LiveRun:
    state: pd.DataFrame
    decisions: pd.DataFrame          # one row per processed candle: action, direction, reason, decision z
    entries: List[dict]
    exits: List[dict]
    engine: PairSignalEngine


def run_live(close_a: pd.Series, close_b: pd.Series, cfg: PairConfig, *, bootstrap_until: Optional[int] = None,
             skip: Sequence[int] = (), confirmed_absent: Sequence[pd.Timestamp] = ()) -> LiveRun:   # claude code changed: Stage 2D Rev B C5 — confirmed_absent: grid candles the (simulated) reconciler confirms are absent from the data source
    """Drive PairSignalEngine as pairs_bot_runner does. Candles at positions
    < bootstrap_until feed bootstrap_candle() (state only); later candles go
    through process_candle(). `skip` = candles the live loop never receives."""
    eng = PairSignalEngine(cfg)
    absent = set(pd.Timestamp(t) for t in confirmed_absent)   # claude code changed: Stage 2D Rev B C5
    rows, dec_rows, entries, exits = {}, [], [], []
    for i, ts in enumerate(close_a.index):
        if i in skip:
            continue
        pa, pb = float(close_a.iloc[i]), float(close_b.iloc[i])
        if bootstrap_until is not None and i < bootstrap_until:
            eng.bootstrap_candle(pa, pb, timestamp=ts)   # claude code changed: Stage 2D Rev B — live bootstrap is timestamped (grid stepping)
            d = None
        else:
            d = eng.process_candle(ts, pa, pb)
            # claude code changed: Stage 2D Rev B C5 — simulated reconciler: a live
            # gap that is ALSO absent from the data source (a data gap research
            # has too) is confirmed; a live-only skip is not, so entries stay blocked.
            if eng.requires_reconciliation and set(eng.reconciliation_gaps) <= absent:
                eng.confirm_gap_reconciled()
        nat = eng._prev_native
        post, P = eng._state.theta, eng._state.P
        e = nat.spread
        rows[ts] = {
            "beta_prior": nat.beta_pred, "alpha_prior": nat.alpha_pred, "innovation": e,
            "beta_post": float(post[0]), "alpha_post": float(post[1]),
            "P00_post": float(P[0, 0]), "P11_post": float(P[1, 1]),
            "P00_prior": nat.beta_uncertainty_prior,           # claude code changed: Stage 2D Rev B — exposed directly
            "P00_decision": nat.beta_uncertainty,              # claude code changed: Stage 2D Rev B — what live's decision path uses
            "S": nat.innovation_variance,                      # claude code changed: Stage 2D Rev B — exposed by live now
            "gain_beta": nat.gain_beta, "gain_alpha": nat.gain_alpha,   # claude code changed: Stage 2D Rev B — exposed directly (was derived from Δβ/e)
            "filtered_residual": nat.filtered_residual,        # claude code changed: Stage 2D Rev B
            "z": np.nan if nat.zscore is None else nat.zscore, "warmup": bool(nat.is_warmup),
        }
        if d is not None:
            dz = d.native.zscore if (d.action == "NONE" and d.reason == "warmup") else None
            dec_rows.append({"ts": ts, "action": d.action, "reason": d.reason, "direction": d.direction})
            if d.action == "ENTER":
                t = d.trade
                entries.append({"fill_ts": ts, "direction": t.direction, "entry_zscore": t.entry_zscore,
                                "entry_beta": t.entry_beta, "entry_spread": t.entry_spread,
                                "entry_alpha": t.entry_alpha, "entry_held_spread": t.entry_held_spread,   # claude code changed: Stage 2D Rev B (D5)
                                "position_usdt": t.position_usdt, "leg_a_usdt": t.leg_a_usdt, "leg_b_usdt": t.leg_b_usdt,
                                "entry_decision_timestamp": t.entry_decision_timestamp,
                                "beta_uncertainty_at_entry": t.beta_uncertainty_at_entry})
            elif d.action == "EXIT":
                exits.append({"fill_ts": ts, "reason": d.reason, "exit_zscore": d.trade.exit_zscore,
                              "hours_held": d.trade.hours_held,
                              "decision_ts": d.trade.exit_decision_timestamp,          # claude code changed: Stage 2D Rev B Steps 9–10
                              "holding_candles": d.trade.holding_candles,              # claude code changed: Stage 2D Rev B
                              "exit_fill_candle_missing": d.trade.exit_fill_candle_missing,   # claude code changed: Stage 2D Rev B C3
                              "reconciliation_affected": d.trade.reconciliation_affected})   # claude code changed: Stage 2D Rev B D-1
    state = pd.DataFrame.from_dict(rows, orient="index")
    return LiveRun(state, pd.DataFrame(dec_rows), entries, exits, eng)


def run_research_trades(close_a: pd.Series, close_b: pd.Series, batch: pd.DataFrame, live_engine: PairSignalEngine,
                        validated_win_rate: float) -> pd.DataFrame:
    """EntryExitEngine with the live engine's own rule parameters (so the
    comparison isolates structural differences, not configuration)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        csv = tmp / "X_USDT_Y_USDT_kalman.csv"
        pd.DataFrame({
            "timestamp": batch.index,
            "kalman_zscore": batch["kalman_zscore"].to_numpy(),
            "kalman_zscore_lag1": batch["kalman_zscore_lag1"].to_numpy(),
            "pair_signal_dynamic": -batch["kalman_zscore"].to_numpy(),
            "kalman_beta": batch["kalman_beta"].to_numpy(),
            "kalman_alpha": batch["kalman_alpha"].to_numpy(),   # claude code changed: Stage 2D Rev B (D5) — α_e source
            "kalman_spread": batch["kalman_spread"].to_numpy(),
            "prediction_error": batch["prediction_error"].to_numpy(),
            "beta_uncertainty": batch["beta_uncertainty"].to_numpy(),
            "close_a": close_a.reindex(batch.index).to_numpy(), "close_b": close_b.reindex(batch.index).to_numpy(),
        }).to_csv(csv, index=False)
        le = live_engine.engine
        research = EntryExitEngine(
            output_dir=str(tmp / "out"), validated_win_rate=validated_win_rate,
            capital_usdt=le.sizer.capital,   # claude code changed: Stage 2D Rev B step 8 — kelly_safety removed with Kelly sizing
            min_position_usdt=le.sizer.min_position_usdt,
            rule_set="CANONICAL_UNCERTIFIED", protocol=le.protocol,   # claude code changed: Stage 2D Rev B Steps 9–10 — the live engine's canonical lifecycle; D-4 — UNCERTIFIED: this harness feeds an explicit shared θ₀ (not a causal canonical dataset), so its output is never labelled canonical
            pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
        )
        trades, _, _ = research.run(kalman_csv=str(csv))
        trades.attrs["entry_sign_mismatches"] = list(research.entry_sign_mismatches)   # claude code changed: Stage 2D Rev B C1
        return trades
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── modelled live P&L (execution layer excluded) ────────────────────────────

def model_live_pnl(entry: dict, exit_: dict, close_a: pd.Series, close_b: pd.Series, costs: PairCosts) -> dict:
    ca, cb = close_a[entry["fill_ts"]], close_b[entry["fill_ts"]]
    pos = open_pair_position(entry["direction"], entry["entry_beta"], entry["position_usdt"], ca, cb, entry["fill_ts"])
    sl, _ = close_pair_slice(pos, 1.0, close_a[exit_["fill_ts"]], close_b[exit_["fill_ts"]], exit_["fill_ts"], costs)
    return {"mark_pnl": sl.mark_pnl, "net_pnl": sl.net_pnl, "qty_a": pos.qty_a, "qty_b": pos.qty_b,
            "entry_price_a": ca, "exit_price_a": close_a[exit_["fill_ts"]]}


# ── full forensic report ─────────────────────────────────────────────────────

@dataclass
class ForensicReport:
    state_fields: List[FieldStat]
    not_measurable: Dict[str, str]
    decision_lag_candles: Optional[int]
    research_trades: pd.DataFrame
    live_entries: List[dict]
    live_exits: List[dict]
    trade_pairs: pd.DataFrame          # matched trades, research vs live, field by field
    first_trade_replay: pd.DataFrame   # candle-by-candle for the first matched trade
    config_differences: Dict[str, Tuple]
    deployment_differences: Dict[str, Tuple] = field(default_factory=dict)   # claude code changed: Stage 2D Rev B — capital/floor (not rules)
    live_entry_sign_mismatches: list = field(default_factory=list)           # claude code changed: Stage 2D Rev B C1
    research_entry_sign_mismatches: list = field(default_factory=list)       # claude code changed: Stage 2D Rev B C1

    def clean_trade_pairs(self) -> pd.DataFrame:   # claude code changed: Stage 2D Rev B D-1/C3 — the only rows that may serve as clean parity evidence
        """Matched trades that are neither reconciliation-affected (live) nor
        data-incomplete (missing exit-fill candle / censored) on either side."""
        tp = self.trade_pairs
        if tp.empty:
            return tp
        keep = (tp["live_fill"].notna() & ~tp["live_reconciliation_affected"].astype(bool)
                & tp["research_data_complete"].astype(bool) & ~tp["live_exit_fill_candle_missing"].astype(bool))
        return tp[keep]

    def state_table(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "field": f.name, "n_both": f.n_both, "only_research": f.only_research, "only_live": f.only_live,
            "max_abs_error": f.max_abs_error, "max_rel_error": f.max_rel_error, "atol": f.atol, "rtol": f.rtol,
            "equal": f.equal, "category_if_unequal": f.category_if_unequal} for f in self.state_fields])


def forensic_replay(close_a: pd.Series, close_b: pd.Series, *, hedge_ratio: float, intercept: float,
                    validated_win_rate: float, capital_usdt: float,
                    research_rows: Optional[Sequence[int]] = None, live_skip: Sequence[int] = (),
                    live_bootstrap_until: Optional[int] = None) -> ForensicReport:
    """research_rows: positions research receives (None = all). Positions not
    in it are a DATA gap for research. live_skip: positions live never
    receives. live_bootstrap_until: live state-only warmup boundary."""
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            idx_r = close_a.index if research_rows is None else close_a.index[list(research_rows)]
            ra, rb = close_a.loc[idx_r], close_b.loc[idx_r]
            batch = research_state(ra, rb, hedge_ratio, intercept)
            cfg = _cfg(hedge_ratio, intercept, validated_win_rate, capital_usdt)
            data_gaps = close_a.index.difference(idx_r)   # claude code changed: Stage 2D Rev B C5 — absent from the data source (research does not have them either)
            live = run_live(close_a, close_b, cfg, bootstrap_until=live_bootstrap_until, skip=live_skip,
                            confirmed_absent=data_gaps)
            trades = run_research_trades(ra, rb, batch, live.engine, validated_win_rate)
    finally:
        logging.disable(logging.NOTSET)

    L = live.state.reindex(batch.index)
    # claude code changed: Stage 2D Rev B — research now carries prediction-only
    # rows for missing grid candles; live steps those internally but the harness
    # only records candles it receives, so state is compared on observed candles
    # (the gap propagation itself shows up in the next observed prior P00).
    batch = batch.assign(**{c: batch[c].where(batch["is_observed"]) for c in batch.columns
                            if c not in ("is_observed", "state_valid", "kalman_segment", "is_warmup", "is_training_window")})
    post_warm = ~batch["is_warmup"]
    fields = [
        compare_numeric("beta_prior", batch["kalman_beta_pred"], L["beta_prior"], "D state"),
        compare_numeric("alpha_prior", batch["kalman_alpha_pred"], L["alpha_prior"], "D state"),
        compare_numeric("innovation", batch["kalman_spread"].where(post_warm), L["innovation"].where(post_warm), "D state"),
        compare_numeric("beta_post", batch["kalman_beta"], L["beta_post"], "D state"),
        compare_numeric("alpha_post", batch["kalman_alpha"], L["alpha_post"], "D state"),
        compare_numeric("P00_post", batch["beta_uncertainty"], L["P00_post"], "D state"),
        compare_numeric("P11_post", batch["alpha_uncertainty"], L["P11_post"], "D state"),
        compare_numeric("gain_beta", batch["kalman_gain_beta"], L["gain_beta"], "A mathematical"),   # claude code changed: Stage 2D Rev B — direct, both sides expose K
        compare_numeric("gain_alpha", batch["kalman_gain_alpha"], L["gain_alpha"], "A mathematical"),   # claude code changed: Stage 2D Rev B
        compare_numeric("innovation_variance_S", batch["innovation_variance"], L["S"], "A mathematical"),   # claude code changed: Stage 2D Rev B
        compare_numeric("P00_prior", batch["beta_uncertainty_prior"], L["P00_prior"], "D state"),   # claude code changed: Stage 2D Rev B
        compare_numeric("filtered_residual", batch["filtered_residual"], L["filtered_residual"], "A mathematical"),   # claude code changed: Stage 2D Rev B
        compare_numeric("zscore", batch["kalman_zscore"], L["z"], "A mathematical"),
        compare_numeric("uncertainty_used_for_decisions", batch["beta_uncertainty"], L["P00_decision"],   # claude code changed: Stage 2D Rev B — live's decision value, whatever it is
                        "A mathematical (prior vs posterior)"),
    ]
    not_measurable = {
        "live_execution_fill_price": "live fills at an exchange ticker after the loop wakes; not replayable offline",
        "live_fees_and_borrow": "booked by bot/pairs/trade_logger from real fills; not replayable offline",
    }

    # decision clock: which lag reproduces live's decision z from research z?
    lag = None
    bz = batch["kalman_zscore"]
    live_dec_z = {}
    for e in live.entries:
        live_dec_z[e["fill_ts"]] = e["entry_zscore"]
    if live_dec_z:
        for k in range(0, 4):
            ok = all(abs(bz.shift(k).get(ts, np.nan) - z) <= 1e-9 for ts, z in live_dec_z.items())
            if ok:
                lag = k
                break

    # match trades: research decision cutoff t ↔ live entry whose decision z equals research z(t)
    pairs = []
    for _, t in trades.iterrows():
        dts = pd.Timestamp(t["entry_decision_timestamp"])
        # live processes candle c and decides on candle c−1's values, so a live entry
        # processed at c corresponds to a decision cutoff at c − lag (measured above;
        # the trade log rounds entry_zscore to 4 dp, so z is not used as the key)
        k = lag if lag is not None else 1
        match = next((e for e in live.entries
                      if e["fill_ts"] - k * HOUR == dts and e["direction"] == t["direction"]), None)
        if match is None:
            pairs.append({"research_decision": dts, "live_fill": None})
            continue
        li = live.entries.index(match)
        lx = live.exits[li] if li < len(live.exits) else None
        modelled = model_live_pnl(match, lx, close_a, close_b, PairCosts(0.001, 0.0005)) if lx else {}
        pairs.append({
            "research_decision": dts, "live_decision_candle": match["fill_ts"] - HOUR,
            "research_fill": pd.Timestamp(t["entry_timestamp"]), "live_fill": match["fill_ts"],
            "research_entry_beta": t["entry_beta"], "live_entry_beta": match["entry_beta"],
            "research_entry_alpha": t.get("entry_alpha"), "live_entry_alpha": match["entry_alpha"],   # claude code changed: Stage 2D Rev B (D5)
            "research_entry_held_spread": t.get("entry_held_spread"), "live_entry_held_spread": match["entry_held_spread"],   # claude code changed: Stage 2D Rev B (D5)
            "live_entry_decision_ts": match["entry_decision_timestamp"],   # claude code changed: Stage 2D Rev B (D5)
            "research_entry_price_a": t["entry_price_a"], "live_entry_price_a": close_a[match["fill_ts"]],
            "research_position_usdt": t["position_usdt"], "live_position_usdt": match["position_usdt"],
            "research_unc_at_entry": t["beta_uncertainty_at_entry"], "live_unc_at_entry": match["beta_uncertainty_at_entry"],
            "research_exit_decision": pd.Timestamp(t["exit_decision_timestamp"]),
            "research_exit_fill": pd.Timestamp(t["exit_timestamp"]),
            "live_exit_fill": lx["fill_ts"] if lx else None,
            "research_exit_reason": t["exit_reason"], "live_exit_reason": lx["reason"] if lx else None,
            "research_holding_candles": t.get("holding_candles"), "live_holding_candles": lx["holding_candles"] if lx else None,   # claude code changed: Stage 2D Rev B Steps 9–10
            "live_exit_decision": lx["decision_ts"] if lx else None,   # claude code changed: Stage 2D Rev B Steps 9–10
            "live_reconciliation_affected": bool(lx["reconciliation_affected"]) if lx else False,   # claude code changed: Stage 2D Rev B D-1
            "research_data_complete": bool(t.get("data_complete", True)),   # claude code changed: Stage 2D Rev B C3
            "live_exit_fill_candle_missing": bool(lx["exit_fill_candle_missing"]) if lx else False,   # claude code changed: Stage 2D Rev B C3
            "research_hours_held": t["hours_held"], "live_hours_held": lx["hours_held"] if lx else None,
            "research_mark_pnl": t["mark_pnl_usdt"], "live_mark_pnl_modelled": modelled.get("mark_pnl"),
            "research_net_pnl": t["net_pnl_usdt"], "live_net_pnl_modelled": modelled.get("net_pnl"),
            "live_entry_record_has_decision_ts": match["entry_decision_timestamp"] is not None,
        })
    trade_pairs = pd.DataFrame(pairs)

    # candle-by-candle replay of the first matched trade
    replay_rows = []
    if not trade_pairs.empty and trade_pairs["live_fill"].notna().any():
        first = trade_pairs[trade_pairs["live_fill"].notna()].iloc[0]
        end = max(first["research_exit_fill"], first["live_exit_fill"] or first["research_exit_fill"])
        for ts in batch.loc[first["research_decision"]:end].index:
            replay_rows.append({
                "ts": ts,
                "research_z": batch.at[ts, "kalman_zscore"], "live_z_native": L.at[ts, "z"],
                "research_beta_post": batch.at[ts, "kalman_beta"], "live_beta_prior": L.at[ts, "beta_prior"],
                "research_event": ("ENTRY_DECISION" if ts == first["research_decision"] else
                                   "ENTRY_FILL" if ts == first["research_fill"] else
                                   "EXIT_DECISION" if ts == first["research_exit_decision"] else
                                   "EXIT_FILL" if ts == first["research_exit_fill"] else ""),
                "live_event": ("ENTER (decides on t-1)" if ts == first["live_fill"] else
                               "EXIT" if ts == first["live_exit_fill"] else ""),
            })

    # claude code changed: Stage 2D Rev B (D10) — RULE-SET differences: a research
    # engine in the canonical rule set (defaults otherwise) vs the live engine.
    # The rules are the rule set name, the protocol hash (Q/R, z, threshold, H,
    # d, g_max, allocation f) and the execution delay. Deployment amounts
    # (capital C, minimum position) are configuration, not rules, and are
    # reported separately in deployment_differences.
    with contextlib.redirect_stdout(io.StringIO()):
        logging.disable(logging.CRITICAL)
        try:
            default = EntryExitEngine(output_dir=tempfile.mkdtemp(), validated_win_rate=validated_win_rate,
                                      rule_set="CANONICAL")
        finally:
            logging.disable(logging.NOTSET)
    le = live.engine.engine
    cfg_diff = {}
    for name, a, b in (("rule_set", default.rule_set, le.rule_set),
                       ("protocol_hash", default.protocol.config_hash(), le.protocol.config_hash()),
                       ("execution_delay_candles", default.execution_delay_candles, le.execution_delay_candles),
                       ("sizer.fraction", default.sizer.fraction, le.sizer.fraction)):
        if a != b:
            cfg_diff[name] = (a, b)
    deployment_diff = {f"sizer.{a}": (getattr(default.sizer, a), getattr(le.sizer, a))
                       for a in ("capital", "min_position_usdt") if getattr(default.sizer, a) != getattr(le.sizer, a)}

    return ForensicReport(fields, not_measurable, lag, trades, live.entries, live.exits,
                          trade_pairs, pd.DataFrame(replay_rows), cfg_diff, deployment_diff,
                          live.engine.entry_sign_mismatches, getattr(trades, "attrs", {}).get("entry_sign_mismatches", []))
