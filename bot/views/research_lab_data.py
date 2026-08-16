# ============================================================
# bot/views/research_lab_data.py
# claude code changed: new file — real data sources for the Research Lab
# interface. Same rule as bot/views/terminal_data.py: every function
# either returns real data read from bot/research/*'s actual persisted
# output, or an explicit {"available": False, "reason": ..., "requires":
# ...} marker naming exactly what backend work is missing. Nothing here
# is invented.
#
# This entire module reads Subsystem B (the offline research pipeline —
# see architecture audit) from disk. It does not run any research code
# live; running cointegration/Kalman/walk-forward/permutation analysis
# takes minutes to hours per pair (see research_data/permutation_test/*/
# shuffle_001..100/ — 100 full re-simulations per pair) and is meant to
# be triggered via the CLI (run_research_all.py etc.), not a page load.
#
# VERDICT TAXONOMY — used throughout, not "AI score":
#   TESTED                    — ran through a stage, no verdict yet
#   STATISTICALLY SUPPORTED   — passed the relevant significance test
#   PROMISING                 — passed a weaker/earlier-stage test only
#   UNSTABLE                  — inconsistent across folds/windows/shuffles
#   REJECTED                  — failed a significance test or filter
#   REQUIRES FURTHER RESEARCH — inconclusive / borderline
# Each classify_* function below documents exactly which real field(s)
# drive its verdict.
# ============================================================

import os
import re
import glob
from datetime import datetime, timezone

import pandas as pd
from django.conf import settings

DATA_DIR = os.path.join(settings.BASE_DIR, "data")
RESEARCH_DATA_DIR = os.path.join(settings.BASE_DIR, "research_data")


# ============================================================
# 1. DATASET EXPLORER
# ============================================================
def get_dataset_explorer():
    """
    Real: every data/*.csv (raw OHLCV per symbol, fetched by
    bot/fetch_all_symbols.py) plus research_data/observations.csv (the
    merged cross-sectional feature+label dataset built by
    bot/research/build_observations.py).
    """
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.csv"))):
        symbol = os.path.basename(path).replace(".csv", "")
        try:
            df = pd.read_csv(path, usecols=["timestamp"])
            n = len(df)
            start = df["timestamp"].iloc[0] if n else None
            end = df["timestamp"].iloc[-1] if n else None
            rows.append({"symbol": symbol, "candles": n, "start": start, "end": end})
        except Exception as e:
            rows.append({"symbol": symbol, "candles": None, "start": None, "end": None, "error": str(e)[:60]})

    obs_path = os.path.join(RESEARCH_DATA_DIR, "observations.csv")
    observations = {"available": False}
    if os.path.exists(obs_path):
        try:
            obs_df = pd.read_csv(obs_path, usecols=["symbol", "timestamp"])
            observations = {
                "available": True,
                "total_rows": len(obs_df),
                "n_symbols": obs_df["symbol"].nunique(),
                "start": obs_df["timestamp"].min(),
                "end": obs_df["timestamp"].max(),
            }
        except Exception as e:
            observations = {"available": False, "reason": str(e)[:80]}

    return {"assets": rows, "n_assets": len(rows), "observations": observations}


# ============================================================
# 2. FEATURE EXPLORER
# ============================================================
def get_feature_explorer():
    """
    Real: every research_data/*_validated_features.csv (per-symbol
    output of bot/research/feature_validator.py). Returns the full
    browsable table, not a top-N slice (that's Feature Explorer's job;
    ranked views live in Predictive Power Analysis below).
    """
    files = sorted(glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv")))
    if not files:
        return {"available": False, "reason": "No *_validated_features.csv in research_data/", "requires": "Run bot/run_research_all.py"}

    frames = []
    for path in files:
        symbol = os.path.basename(path).replace("_validated_features.csv", "")
        try:
            df = pd.read_csv(path)
            df["symbol"] = symbol
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return {"available": False, "reason": "Feature files present but unreadable"}

    all_df = pd.concat(frames, ignore_index=True)
    all_df["verdict"] = all_df.apply(classify_feature, axis=1)

    feature_names = sorted(all_df["feature"].unique().tolist())
    symbols = sorted(all_df["symbol"].unique().tolist())

    return {
        "available": True,
        "rows": all_df.to_dict("records"),
        "n_rows": len(all_df),
        "n_features": len(feature_names),
        "n_symbols": len(symbols),
        "feature_names": feature_names,
        "verdict_counts": all_df["verdict"].value_counts().to_dict(),
    }


def classify_feature(row):
    """
    Real classification from bot/research/feature_validator.py's own
    output columns — valid, passes_fdr, passes_bonferroni, recommendation.
    No new scoring logic invented: 'valid' and 'passes_fdr' are already
    computed by feature_validator.py itself; this only maps their
    combination onto the six-state verdict vocabulary.
    """
    valid = bool(row.get("valid"))
    passes_fdr = bool(row.get("passes_fdr"))
    passes_bonf = bool(row.get("passes_bonferroni"))
    recommendation = str(row.get("recommendation", "")).upper()

    if valid and passes_fdr and passes_bonf:
        return "STATISTICALLY SUPPORTED"
    if valid and passes_fdr:
        return "PROMISING"
    if recommendation == "DELETE":
        return "REJECTED"
    if recommendation == "REVIEW":
        return "REQUIRES FURTHER RESEARCH"
    return "TESTED"


# ============================================================
# 3. FEATURE STABILITY ANALYSIS
# ============================================================
def get_feature_stability():
    """
    Real, partial: stability_std is already a column in
    *_validated_features.csv (computed by feature_validator.py itself —
    std of the feature's IC across sub-periods). The DEEPER per-window/
    yearly breakdown that bot/research/feature_stability_analyzer.py can
    produce (*_stability_yearly.csv, *_stability_rolling.csv,
    *_stability_summary.csv) has not been run in this environment — no
    such files exist under research_data/ yet, confirmed by direct
    filesystem check, not assumed.
    """
    files = sorted(glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv")))
    stability_rows = []
    for path in files:
        symbol = os.path.basename(path).replace("_validated_features.csv", "")
        try:
            df = pd.read_csv(path, usecols=["feature", "stability_std", "ic_overall", "valid"])
            df["symbol"] = symbol
            stability_rows.append(df)
        except Exception:
            continue

    basic_available = bool(stability_rows)
    basic_df = pd.concat(stability_rows, ignore_index=True) if stability_rows else pd.DataFrame()
    if basic_available:
        basic_df["verdict"] = basic_df.apply(
            lambda r: "UNSTABLE" if pd.notna(r["stability_std"]) and abs(r["stability_std"]) > 0.15
            else ("STATISTICALLY SUPPORTED" if r["valid"] else "TESTED"),
            axis=1,
        )

    deep_files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_stability_*.csv"))

    return {
        "basic_available": basic_available,
        "rows": basic_df.to_dict("records") if basic_available else [],
        "deep_available": bool(deep_files),
        "deep_files": [os.path.basename(f) for f in deep_files],
        "deep_requires": "python -m bot.research.feature_stability_analyzer (writes {symbol}_stability_yearly.csv / _stability_rolling.csv / _stability_summary.csv to research_data/ — none present yet)",
    }


# ============================================================
# 4. PREDICTIVE POWER ANALYSIS
# ============================================================
def get_predictive_power():
    """
    Real: full IC/significance battery already computed by
    feature_validator.py — ic_overall, ic_rank, p_value_mannwhitney,
    p_bonferroni, p_fdr, passes_fdr, passes_bonferroni, sharpe_contrib,
    econ_significant, direction. Ranked by |ic_overall|.
    """
    files = sorted(glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv")))
    if not files:
        return {"available": False, "reason": "No *_validated_features.csv in research_data/"}

    frames = []
    for path in files:
        symbol = os.path.basename(path).replace("_validated_features.csv", "")
        try:
            df = pd.read_csv(path)
            df["symbol"] = symbol
            frames.append(df)
        except Exception:
            continue

    all_df = pd.concat(frames, ignore_index=True)
    all_df["abs_ic"] = all_df["ic_overall"].abs()
    all_df["verdict"] = all_df.apply(classify_feature, axis=1)
    ranked = all_df.sort_values("abs_ic", ascending=False)

    return {
        "available": True,
        "top_by_ic": ranked.head(25).to_dict("records"),
        "n_total": len(all_df),
        "n_econ_significant": int(all_df["econ_significant"].sum()) if "econ_significant" in all_df else 0,
        "n_passes_fdr": int(all_df["passes_fdr"].sum()) if "passes_fdr" in all_df else 0,
    }


# ============================================================
# 5. CROSS-ASSET ANALYSIS
# ============================================================
def get_cross_asset_analysis():
    """
    Real: research_data/cointegration_pairs.csv (191 pairs, from
    bot/research/cointegration_engine.py — p-values, hedge ratio,
    half-life, is_cointegrated, passes_filters, reject_reason) plus
    latest cross-sectional z-scores from *_cross_section.csv (reused
    logic from terminal_data.get_cross_sectional_dispersion).
    """
    coint_path = os.path.join(RESEARCH_DATA_DIR, "cointegration_pairs.csv")
    if not os.path.exists(coint_path):
        pairs_result = {"available": False, "reason": "cointegration_pairs.csv not found"}
    else:
        try:
            df = pd.read_csv(coint_path)
            df["verdict"] = df.apply(classify_pair, axis=1)
            pairs_result = {
                "available": True,
                "n_pairs": len(df),
                "n_cointegrated": int(df["is_cointegrated"].sum()),
                "n_passes_filters": int(df["passes_filters"].sum()),
                "top_pairs": df.sort_values("coint_pvalue").head(20).to_dict("records"),
                "verdict_counts": df["verdict"].value_counts().to_dict(),
            }
        except Exception as e:
            pairs_result = {"available": False, "reason": str(e)[:80]}

    return {"cointegration": pairs_result}


def classify_pair(row):
    """
    Real classification from cointegration_engine.py's own output —
    is_cointegrated (ADF/coint p-value test) and passes_filters (also
    applies the half-life ceiling). A pair cointegrated but failing
    filters (e.g. half-life too long) is UNSTABLE — statistically
    related but not tradeable on the timeframe this pipeline targets.
    """
    if not bool(row.get("is_cointegrated")):
        return "REJECTED"
    if not bool(row.get("passes_filters")):
        return "UNSTABLE"
    return "TESTED"


# ============================================================
# 6. REGIME ANALYSIS
# ============================================================
def get_regime_analysis(backtest_results):
    """
    Real: regime_breakdown / structure_breakdown / route_stats, already
    computed by Backtester._build_results() for whichever symbol the
    main dashboard last backtested (bot/backtesting/backtester.py). This
    reuses that result rather than re-running a backtest — Research Lab
    does not duplicate the backtest itself.
    """
    if not backtest_results:
        return {"available": False, "reason": "No cached backtest result available"}

    return {
        "available": True,
        "regime_breakdown": backtest_results.get("regime_breakdown", {}),
        "structure_breakdown": backtest_results.get("structure_breakdown", {}),
        "route_stats": backtest_results.get("route_stats", {}),
    }


# ============================================================
# 7. STRATEGY COMPARISON
# ============================================================
def get_strategy_comparison():
    """
    Real: research_data/walk_forward/walk_forward_leaderboard.csv (from
    bot/research/walk_forward_engine.py) — multi-pair OOS comparison
    with a real 'passed' boolean already computed there. Cross-referenced
    with per-pair *_strategy_summary.csv (full-period stats +
    entry_ic/entry_ic_pvalue from bot/research/entry_exit_engine.py),
    found via recursive glob since these live in different subfolders
    per pair (research_data/ vs. research_data/permutation_test/*/real_run/).
    """
    leaderboard_path = os.path.join(RESEARCH_DATA_DIR, "walk_forward", "walk_forward_leaderboard.csv")
    leaderboard = {"available": False}
    if os.path.exists(leaderboard_path):
        try:
            df = pd.read_csv(leaderboard_path)
            # claude code changed: walk_forward_engine.py writes oos_win_rate
            # as a 0-1 fraction (n_winners/n_total), but every other win-rate
            # field in this app (bot/backtesting/backtester.py's own
            # win_rate) is 0-100 — the templates all assume 0-100 and append
            # "%", so a real 69% win rate was rendering as "0.690%". Rescale
            # here, once, at the data layer, rather than special-casing the
            # template.
            if "oos_win_rate" in df.columns:
                df["oos_win_rate"] = (df["oos_win_rate"] * 100).round(4)
            df["verdict"] = df.apply(classify_walk_forward, axis=1)
            leaderboard = {"available": True, "rows": df.to_dict("records")}
        except Exception as e:
            leaderboard = {"available": False, "reason": str(e)[:80]}

    summary_files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "**", "*_strategy_summary.csv"), recursive=True)
    summaries = []
    seen_pairs = set()
    for path in summary_files:
        try:
            df = pd.read_csv(path)
            # claude code changed: same 0-1-vs-0-100 win_rate inconsistency
            # as oos_win_rate above — entry_exit_engine.py writes win_rate
            # as a 0-1 fraction here.
            if "win_rate" in df.columns:
                df["win_rate"] = (df["win_rate"] * 100).round(4)
            for _, row in df.iterrows():
                pair = row.get("pair")
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                summaries.append(row.to_dict())
        except Exception:
            continue

    return {"leaderboard": leaderboard, "strategy_summaries": summaries}


def classify_walk_forward(row):
    """Real: walk_forward_engine.py's own 'passed' boolean, already gating on OOS win rate/Sharpe/profit-factor thresholds not loosening out-of-sample."""
    if bool(row.get("passed")):
        return "PROMISING"   # cleared walk-forward; still needs the permutation test (see Robustness Testing) before "statistically supported"
    return "UNSTABLE"


# ============================================================
# 8. BACKTEST RESULTS
# ============================================================
def get_backtest_results(symbol, backtest_results):
    """
    Real: (a) the live single-symbol backtest already computed for the
    main dashboard (bot/backtesting/backtester.py, same result dict);
    (b) walk-forward OOS equity/trade CSVs per research pair (real
    simulated trade-by-trade output, not summary stats).
    """
    live = None
    if backtest_results:
        live = {
            "symbol": symbol,
            "total_trades": backtest_results.get("total_trades"),
            "win_rate": backtest_results.get("win_rate"),
            "sharpe_ratio": backtest_results.get("sharpe_ratio"),
            "profit_factor": backtest_results.get("profit_factor"),
            "max_drawdown": backtest_results.get("max_drawdown"),
        }

    oos_dirs = glob.glob(os.path.join(RESEARCH_DATA_DIR, "walk_forward", "*"))
    oos_pairs = []
    for d in oos_dirs:
        if not os.path.isdir(d):
            continue
        pair_name = os.path.basename(d)
        equity_files = glob.glob(os.path.join(d, "*_oos_equity.csv"))
        trades_files = glob.glob(os.path.join(d, "*_oos_trades.csv"))
        if equity_files:
            try:
                eq = pd.read_csv(equity_files[0])
                n_trades = None
                if trades_files:
                    n_trades = len(pd.read_csv(trades_files[0]))
                # claude code changed: was eq.iloc[-1, -1] (last column),
                # which happened to be drawdown_pct, not equity — real
                # column is 'cumulative_usdt'
                final_equity = float(eq["cumulative_usdt"].iloc[-1]) if len(eq) and "cumulative_usdt" in eq.columns else None
                max_dd = float(eq["drawdown_pct"].min()) if "drawdown_pct" in eq.columns else None   # claude code changed: drawdown_pct is stored negative (0 = no drawdown), so worst point is .min() not .max()
                oos_pairs.append({
                    "pair": pair_name,
                    "n_equity_points": len(eq),
                    "n_oos_trades": n_trades,
                    "final_equity": final_equity,
                    "max_drawdown_pct": max_dd,
                })
            except Exception:
                continue

    return {"live": live, "oos_pairs": oos_pairs}


# ============================================================
# 9. ROBUSTNESS TESTING
# ============================================================
def get_robustness_testing():
    """
    Real: research_data/permutation_test/*/permutation_verdict.csv (from
    bot/research/permutation_test_engine.py — 100-shuffle null
    distribution per pair) is the primary robustness signal. Walk-forward
    efficiency (from Strategy Comparison's leaderboard) is a secondary,
    weaker robustness signal — cross-referenced here so a researcher can
    see both instead of just one collapsed number.
    """
    verdict_files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "permutation_test", "*", "permutation_verdict.csv"))
    rows = []
    for path in verdict_files:
        pair_name = os.path.basename(os.path.dirname(path))
        try:
            df = pd.read_csv(path)
            if len(df):
                row = df.iloc[0].to_dict()
                # claude code changed: same 0-1-vs-0-100 win_rate
                # inconsistency as get_strategy_comparison() above —
                # permutation_test_engine.py writes win_rate as a 0-1
                # fraction (its own debug output formats it with ":.1%").
                # win_rate_percentile is a genuinely different, already
                # 0-100-scale field (percentile rank vs. the shuffle
                # distribution) and is left untouched.
                if "win_rate" in row and row["win_rate"] is not None:
                    row["win_rate"] = round(row["win_rate"] * 100, 4)
                row["pair"] = pair_name
                row["verdict"] = classify_permutation(row)
                rows.append(row)
        except Exception:
            continue

    return {
        "available": bool(rows),
        "rows": rows,
        "n_pairs_tested": len(rows),
        "n_shuffles_per_pair": 100,  # fixed by permutation_test_engine.py's design — real, documented constant, not measured per-run
    }


def classify_permutation(row):
    """
    Real: permutation_test_engine.py's own edge_appears_real field (the
    real, unshuffled result must beat the 100-shuffle null distribution
    on win_rate/Sharpe/profit_factor AND show a meaningfully positive
    entry_ic). This is the single strictest test in the whole pipeline —
    both AVAX/ATOM and DOT/LINK fail it, matching the REJECTED verdicts
    already recorded in model_governance_log.md (see Research
    Conclusions) despite one of them passing walk-forward.
    """
    if bool(row.get("edge_appears_real")):
        return "STATISTICALLY SUPPORTED"
    return "REJECTED"


# ============================================================
# 10. RESEARCH CONCLUSIONS
# ============================================================
def get_research_conclusions(verdict_filter=None):
    """
    Real: parses research_data/model_governance_log.md — the project's
    actual, human-written go/no-go decision log (bot/research/* pairs
    trading track). Parsing is structural (splits on '## ' headers,
    extracts **Date:**/**Verdict:** lines) — the reasoning text itself
    is not re-derived or summarized by this function, just extracted.

    claude code changed: added verdict_filter (e.g. "REJECTED") so the
    Rejected Research page can call this exact function instead of a
    second parser — Research Archive calls it unfiltered. One data
    source, two views.
    """
    path = os.path.join(RESEARCH_DATA_DIR, "model_governance_log.md")
    if not os.path.exists(path):
        return {"available": False, "reason": "model_governance_log.md not found"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"available": False, "reason": str(e)[:80]}

    sections = content.split("\n## ")
    entries = []
    for section in sections[1:]:  # skip the file's own leading title block
        lines = section.split("\n")
        title = lines[0].strip()

        date_match = re.search(r"\*\*Date:\*\*\s*([\d-]+)", section)
        verdict_match = re.search(r"\*\*Verdict:\*\*\s*([^\n]+)", section)

        # Real mapping onto the six-state vocabulary from the log's own
        # verdict text, which already uses these words explicitly
        verdict_text = verdict_match.group(1) if verdict_match else ""
        verdict_upper = verdict_text.upper()
        if "REJECTED" in verdict_upper:
            verdict_tag = "REJECTED"
        elif "VALIDATED" in verdict_upper or "ACCEPTED" in verdict_upper:
            verdict_tag = "STATISTICALLY SUPPORTED"
        elif "REVIEW" in verdict_upper or "INCONCLUSIVE" in verdict_upper:
            verdict_tag = "REQUIRES FURTHER RESEARCH"
        else:
            verdict_tag = "TESTED"

        # First paragraph of body text after the title/date/verdict block,
        # as a real excerpt (not a generated summary)
        body_start = section.find("\n\n", section.find("**Verdict:**"))
        excerpt = section[body_start:body_start + 500].strip() if body_start != -1 else ""

        entries.append({
            "title": title,
            "date": date_match.group(1) if date_match else None,
            "verdict_text": verdict_text.strip(),
            "verdict_tag": verdict_tag,
            "excerpt": excerpt,
        })

    if verdict_filter:
        entries = [e for e in entries if e["verdict_tag"] == verdict_filter]

    return {"available": True, "entries": entries, "n_entries": len(entries)}
