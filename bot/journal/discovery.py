# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/discovery.py
#
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 7 (owner: "statsmodels logistic regression
# only ... deliberately minimal and interpretable. No ML output may directly modify strategy, execution, risk, or live
# behavior. Any discovered relationship remains a research hypothesis").
#
# What it does: for ONE labelled backtest run, zone S only (through analytics.read_labels — the zone guard),
# one row per opportunity episode (OD-J14 first decision of each break-at-exit episode), target = the hypothetical
# rule's net R > 0 (stop-first baseline), features = point-in-time SIMULATED_CAUSAL snapshot values that exist for
# every decision (regime ADX / ATR ratio / EMA spread / BB width / volatility-extreme flag, direction, hour of day).
# It fits a standardised logistic regression in purged walk-forward folds (bot.research.oos_validator.build_folds;
# purge AND embargo = the most episodes inside any 24 h label window) and reports out-of-fold log-loss vs the base
# rate and AUC, plus coefficient-sign stability across folds.
# Output: a JSON file (never overwritten) + provenance sidecar listing CANDIDATE hypotheses for OWNER REVIEW. Nothing
# is registered automatically (registration needs a human economic rationale, D-3), nothing is imported by any
# strategy/engine/execution module (import-boundary tests), and the evidence level is at most OBSERVATION.
# Human annotations are never features (no NLP pipeline exists).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

DISCOVERY_VERSION = "QBP-JRNL-DISCOVERY-1"
MIN_EPISODES = 200                    # below this a model is not fitted (descriptive tables only)
STABILITY_THRESHOLD = 0.8             # a coefficient must keep its sign in >= 80 % of folds to be a candidate
FEATURES = ("regime_adx", "regime_atr_ratio", "regime_ema_spread_pct", "regime_bb_width_pct",
            "regime_volatility_extreme", "direction_long", "hour_sin", "hour_cos")
LABEL_WINDOW = pd.Timedelta(hours=24)
OUTPUT_DIR = Path("research_data") / "journal_discovery"


class DiscoveryRefused(RuntimeError):
    pass


def episode_frame(run) -> pd.DataFrame:
    """One row per break-at-exit episode (its first decision), zone S, with features and the binary target."""
    from bot.journal.analytics import assign_episodes, coverage_by_index, read_labels
    from bot.journal.models import DecisionEvent, MarketSnapshot
    if run.environment != "BACKTEST":
        raise DiscoveryRefused("discovery runs on backtest journal runs only (live/dry-run outcomes are sealed, OD-J4)")
    labels = {l.decision_id: l for l in read_labels(run).filter(label_type="COUNTERFACTUAL_RULE",
                                                                variant="BASELINE_STOP_FIRST", status="OK")
              if l.net_r is not None}
    decs = list(DecisionEvent.objects.filter(run=run).order_by("candle_index"))
    brk, _ = assign_episodes(decs, coverage_by_index(run), pd.Timedelta(run.timeframe))
    snaps = {s.decision_id: s for s in MarketSnapshot.objects.filter(decision__run=run, snapshot_kind="SIMULATED_CAUSAL")}
    rows, seen = [], set()
    for d in decs:
        ep = brk.get(d.decision_id)
        if ep is None or ep in seen or d.decision_id not in labels or d.decision_id not in snaps:
            continue
        seen.add(ep)
        v = snaps[d.decision_id].values
        t = pd.Timestamp(d.candle_open_time)
        rows.append({"t": t, "decision_id": str(d.decision_id), "y": int(labels[d.decision_id].net_r > 0),
                     "regime_adx": v.get("regime_adx"), "regime_atr_ratio": v.get("regime_atr_ratio"),
                     "regime_ema_spread_pct": v.get("regime_ema_spread_pct"), "regime_bb_width_pct": v.get("regime_bb_width_pct"),
                     "regime_volatility_extreme": float(bool(v.get("regime_volatility_extreme"))),
                     "direction_long": float(d.direction == "LONG"),
                     "hour_sin": math.sin(2 * math.pi * t.hour / 24), "hour_cos": math.cos(2 * math.pi * t.hour / 24)})
    return pd.DataFrame(rows).dropna().sort_values("t").reset_index(drop=True) if rows else pd.DataFrame()


def _auc(y, p) -> Optional[float]:
    y, p = np.asarray(y), np.asarray(p)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.r_[pos, neg]).rank().to_numpy()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _logloss(y, p) -> float:
    p = np.clip(np.asarray(p, float), 1e-12, 1 - 1e-12)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def discover(run, *, write: bool = True, out_dir: Path = OUTPUT_DIR) -> dict:
    import statsmodels.api as sm
    from bot.research.oos_validator import WalkForwardConfig, build_folds
    df = episode_frame(run)
    if len(df) < MIN_EPISODES:
        raise DiscoveryRefused(f"{len(df)} zone-S episodes with outcomes (< {MIN_EPISODES}): too few for a model — "
                               f"use the descriptive tables")
    t = pd.DatetimeIndex(df["t"])
    overlap = int(max(((t >= x) & (t < x + LABEL_WINDOW)).sum() for x in t))     # episodes sharing one label window
    n = len(df)
    cfg = WalkForwardConfig(mode="expanding", min_train_periods=max(100, n // 3), test_periods=max(30, n // 6),
                            horizon=overlap, purge_periods=overlap, embargo_periods=overlap, min_test_periods=20)
    folds = build_folds(pd.DatetimeIndex(df["t"]), cfg)
    X_all, y_all = df[list(FEATURES)].to_numpy(float), df["y"].to_numpy(int)
    oof_p, oof_y, base_p, signs = [], [], [], []
    for f in folds:
        tr = slice(f.train_start_pos, f.train_end_pos)
        te = slice(f.test_start_pos, f.test_end_pos)
        Xtr, ytr, Xte, yte = X_all[tr], y_all[tr], X_all[te], y_all[te]
        if len(set(ytr)) < 2 or len(yte) == 0:
            continue
        mu, sd = Xtr.mean(0), Xtr.std(0)
        sd[sd == 0] = 1.0
        keep = Xtr.std(0) > 0
        try:
            m = sm.Logit(ytr, sm.add_constant((Xtr[:, keep] - mu[keep]) / sd[keep]), missing="raise").fit(disp=0, maxiter=200)
        except Exception:
            continue
        p = m.predict(sm.add_constant((Xte[:, keep] - mu[keep]) / sd[keep], has_constant="add"))
        oof_p.extend(p); oof_y.extend(yte); base_p.extend([ytr.mean()] * len(yte))
        coef = np.zeros(len(FEATURES)); coef[keep] = np.sign(m.params[1:])
        signs.append(coef)
    if not signs:
        raise DiscoveryRefused("no fold could be fitted")
    signs = np.array(signs)
    stability = {f: float(max((signs[:, k] > 0).mean(), (signs[:, k] < 0).mean())) for k, f in enumerate(FEATURES)}
    direction = {f: ("+" if (signs[:, k] > 0).mean() >= (signs[:, k] < 0).mean() else "-") for k, f in enumerate(FEATURES)}
    ll_model, ll_base = _logloss(oof_y, oof_p), _logloss(oof_y, base_p)
    beats = ll_model < ll_base
    candidates = [{"feature": f, "sign": direction[f], "sign_stability": stability[f],
                   "statement_template": f"P(rule net R > 0) {'rises' if direction[f] == '+' else 'falls'} with {f} "
                                         f"(zone S, run {run.run_id}) — needs a written economic rationale before registration"}
                  for f in FEATURES if beats and stability[f] >= STABILITY_THRESHOLD]
    out = {"version": DISCOVERY_VERSION, "run_id": run.run_id, "zones": ["SELECTION"], "unit": "episode (OD-J14 break-at-exit)",
           "target": "rule net R > 0 (stop-first baseline, hypothetical, NOT executed)", "features": list(FEATURES),
           "n_episodes": n, "base_rate": float(y_all.mean()), "n_folds": int(len(signs)),
           "fold_config": cfg.to_dict(), "oof_logloss_model": ll_model, "oof_logloss_base_rate": ll_base,
           "oof_auc": _auc(oof_y, oof_p), "model_beats_base_rate_out_of_fold": bool(beats),
           "sign_stability": stability, "candidates": candidates,
           "status": ("CANDIDATES FOR OWNER REVIEW — exploratory (zone S); not registered, not evidence; ladder level "
                      "at most OBSERVATION; must pass the D-3 registry, zone-V/F confirmation and all five gates"),
           "never_used_by": "strategies, router, execution, risk or the live loop (import-boundary tested)"}
    if write:
        from bot.quant_core.provenance import write_research_output_provenance
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{run.run_id}_{DISCOVERY_VERSION}_{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%S}.json"
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)
        side = write_research_output_provenance(path, kind="JOURNAL_DISCOVERY", producer="bot.journal.discovery",
                                                inputs={"scheme": "JOURNAL_RUN", "version": run.data_version, "run_id": run.run_id},
                                                config={"discovery_version": DISCOVERY_VERSION, "features": list(FEATURES),
                                                        "min_episodes": MIN_EPISODES, "stability": STABILITY_THRESHOLD})
        if side is None:
            raise RuntimeError(f"provenance sidecar not written for {path}")
        out["output_path"] = str(path)
    return out
