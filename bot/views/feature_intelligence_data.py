# ============================================================
# bot/views/feature_intelligence_data.py
# claude code changed: new file — data layer for the Feature Intelligence
# module. Same rule as bot/views/research_lab_data.py: every function
# either returns real data read from bot/research/*'s actual persisted
# output, or an explicit {"available": False, "reason": ...} marker.
# Nothing here is invented; anywhere a number would have to be guessed,
# the field is left out and the template renders "Not calculated".
#
# FEATURE FAMILIES (there is no single canonical feature list in this
# codebase — these are the four real, non-overlapping vocabularies
# found across bot/research/* and bot/engines/regime_detector.py):
#   SINGLE_ASSET_INDICATOR — bot/research/feature_calculator.py output,
#       tested per-symbol by feature_validator.py / feature_decay_analyzer.py
#   CROSS_SECTIONAL        — bot/research/cross_section_engine.py output,
#       tested once against the merged observations.csv (not per-symbol)
#   PAIR_SPREAD            — Kalman/cointegration spread features, only
#       computed for AVAX_USDT/ATOM_USDT and DOT_USDT/LINK_USDT; only
#       AVAX_USDT_ATOM_USDT has ever been run through the stability
#       analyzer (research_data/temporal/AVAX_ATOM_KALMAN_stability_summary.csv)
#   LIVE_REGIME_INDICATOR  — bot/engines/regime_detector.py's RegimeResult
#       fields, used for live signal routing only. Never persisted to
#       research_data/, never run through any validator. Always UNTESTED.
#
# RESEARCH STATUS TAXONOMY (distinct from Research Lab's six-state
# verdict vocabulary — this one is feature-scoped, not pipeline-stage-
# scoped):
#   UNTESTED             — never run through any validator/analyzer
#   UNDER_RESEARCH        — has output, doesn't yet clear a bar either way
#   PROMISING             — passed an earlier/weaker-stage test only
#   STABLE                — decay/stability trend is flat-or-improving
#                           AND predictive signal is real AND not rejected
#   UNSTABLE              — trend is decaying/volatile, or a downstream
#                           permutation test overrides an earlier pass
#   REJECTED              — recommendation=DELETE on most symbols, or
#                           fails a downstream significance test
#   REQUIRES_VALIDATION   — conflicting signals across symbols/tests
# classify_feature_status() documents exactly which real field(s) drive
# each branch.
# ============================================================

import os
import re
import glob
import dataclasses
from collections import Counter

import pandas as pd
from django.core.cache import cache

from bot.views.research_lab_data import DATA_DIR, RESEARCH_DATA_DIR
from bot.engines.regime_detector import RegimeResult

TEMPORAL_DIR = os.path.join(RESEARCH_DATA_DIR, "temporal")

# Verbatim from bot/research/feature_validator.py's own exclude_cols set —
# this is *why* adx/realized_vol never appear in any *_validated_features.csv:
# the validator itself never considers them candidate features.
VALIDATOR_EXCLUDE_COLS = {
    "symbol", "timeframe", "timestamp", "market_regime",
    "forward_return_1h", "forward_return_4h", "forward_return_24h",
    "win_1h", "win_4h", "win_24h",
    "close", "open", "high", "low", "volume",
    "realized_vol", "adx",
}

# Non-feature columns only (OHLCV + labels) — used to find columns that
# feature_calculator.py computes but that feature_validator.py chose to
# exclude (adx, realized_vol), which VALIDATOR_EXCLUDE_COLS above would
# hide if used directly for this purpose.
OHLCV_AND_LABEL_COLS = {
    "symbol", "timeframe", "timestamp", "market_regime",
    "forward_return_1h", "forward_return_4h", "forward_return_24h",
    "win_1h", "win_4h", "win_24h",
    "close", "open", "high", "low", "volume",
}

REDUNDANCY_THRESHOLD = 0.8  # |corr| at/above this is flagged a redundancy candidate — documented, not hidden


# ============================================================
# RAW READERS — one per real file family, no aggregation here
# ============================================================
def discover_single_asset_symbols():
    files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv"))
    return sorted(os.path.basename(f).replace("_validated_features.csv", "") for f in files)


def discover_observation_symbols():
    files = glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_observations.csv"))
    return sorted(os.path.basename(f).replace("_observations.csv", "") for f in files)


def _read_validated_features_all():
    frames = []
    for path in sorted(glob.glob(os.path.join(RESEARCH_DATA_DIR, "*_validated_features.csv"))):
        symbol = os.path.basename(path).replace("_validated_features.csv", "")
        try:
            df = pd.read_csv(path)
            df["symbol"] = symbol
            frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _read_institutional_features():
    path = os.path.join(RESEARCH_DATA_DIR, "validated_features_institutional.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _read_decay_data_all():
    frames = []
    for path in sorted(glob.glob(os.path.join(TEMPORAL_DIR, "*_decay_data.csv"))):
        if "_KALMAN_" in os.path.basename(path):
            continue
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _read_regime_breakdown_all():
    frames = []
    for path in sorted(glob.glob(os.path.join(TEMPORAL_DIR, "*_regime_breakdown.csv"))):
        if "_KALMAN_" in os.path.basename(path):
            continue
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _parse_decay_trends(symbol):
    """
    Real: bot/research/feature_decay_analyzer.py computes a per-feature
    trend classification (_determine_trend) but only ever writes it into
    the plain-text {symbol}_decay_report.txt ("-> Trend: STABLE"), never
    into decay_data.csv. Parsed structurally here, not re-derived.
    """
    path = os.path.join(TEMPORAL_DIR, f"{symbol}_decay_report.txt")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return {}

    trends = {}
    for match in re.finditer(r"Feature:\s*(\w+).*?Trend:\s*(\w+)", content, re.DOTALL):
        trends[match.group(1)] = match.group(2)
    return trends


def _read_pair_stability_summary():
    frames = []
    for path in sorted(glob.glob(os.path.join(TEMPORAL_DIR, "*_stability_summary.csv"))):
        try:
            df = pd.read_csv(path)
            frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _read_permutation_verdicts():
    """Real: research_data/permutation_test/{PAIR}/permutation_verdict.csv, keyed by pair folder name."""
    out = {}
    for path in glob.glob(os.path.join(RESEARCH_DATA_DIR, "permutation_test", "*", "permutation_verdict.csv")):
        pair = os.path.basename(os.path.dirname(path))
        try:
            df = pd.read_csv(path)
            if len(df):
                out[pair] = df.iloc[0].to_dict()
        except Exception:
            continue
    return out


def get_live_regime_feature_names():
    """
    Real: bot/engines/regime_detector.py's RegimeResult dataclass fields,
    read via introspection (not hardcoded) so this never drifts from the
    actual live signal-routing code. Only the float-typed fields are
    "features" in the predictive sense — regime/confidence/summary are
    categorical labels, not indicators.
    """
    return [f.name for f in dataclasses.fields(RegimeResult) if f.type is float]


def get_untested_single_asset_features():
    """
    Real, derived: numeric columns present in {symbol}_observations.csv
    (bot/research/feature_calculator.py's output) that never appear in
    any *_validated_features.csv, *_decay_data.csv, or *_regime_breakdown.csv.
    Confirmed today to be exactly {adx, realized_vol} — both excluded
    from feature_validator.py's own candidate set (see
    VALIDATOR_EXCLUDE_COLS above), and never picked up by the decay
    analyzer either.
    """
    symbols = discover_observation_symbols()
    if not symbols:
        return []
    sample_path = os.path.join(RESEARCH_DATA_DIR, f"{symbols[0]}_observations.csv")
    try:
        sample = pd.read_csv(sample_path, nrows=500)
    except Exception:
        return []

    numeric_cols = [
        c for c in sample.columns
        if c not in OHLCV_AND_LABEL_COLS and pd.api.types.is_numeric_dtype(sample[c])
    ]

    validated = _read_validated_features_all()
    decay = _read_decay_data_all()
    regime = _read_regime_breakdown_all()
    tested = set()
    if len(validated):
        tested |= set(validated["feature"].unique())
    if len(decay):
        tested |= set(decay["feature"].unique())
    if len(regime):
        tested |= set(regime["feature"].unique())

    return [c for c in numeric_cols if c not in tested]


# ============================================================
# FEATURE REGISTRY — the aggregated, per-feature real-data record
# that every panel in this module is built from
# ============================================================
_REGISTRY_CACHE_KEY = "feature_intel_registry_v1"


def build_feature_registry(use_cache=True):
    if use_cache:
        cached = cache.get(_REGISTRY_CACHE_KEY)
        if cached is not None:
            return cached

    validated = _read_validated_features_all()
    decay = _read_decay_data_all()
    regime = _read_regime_breakdown_all()
    institutional = _read_institutional_features()
    pair_stability = _read_pair_stability_summary()
    permutation_verdicts = _read_permutation_verdicts()
    trends_by_symbol = {s: _parse_decay_trends(s) for s in discover_single_asset_symbols()}

    registry = []

    # ---- SINGLE_ASSET_INDICATOR: tested (validator and/or decay) ----
    tested_names = set()
    if len(validated):
        tested_names |= set(validated["feature"].unique())
    if len(decay):
        tested_names |= set(decay["feature"].unique())

    for name in sorted(tested_names):
        v_rows = validated[validated["feature"] == name] if len(validated) else pd.DataFrame()
        d_rows = decay[decay["feature"] == name] if len(decay) else pd.DataFrame()
        r_rows = regime[regime["feature"] == name] if len(regime) else pd.DataFrame()

        symbols_validated = sorted(v_rows["symbol"].unique().tolist()) if len(v_rows) else []
        symbols_decay = sorted(d_rows["symbol"].unique().tolist()) if len(d_rows) else []

        trend_counts = Counter()
        for sym in symbols_decay:
            t = trends_by_symbol.get(sym, {}).get(name)
            if t:
                trend_counts[t] += 1

        latest_year_grades = Counter()
        yearly_ic_by_symbol = {}
        if len(d_rows):
            for sym, sym_rows in d_rows.groupby("symbol"):
                sym_rows = sym_rows.sort_values("year")
                yearly_ic_by_symbol[sym] = dict(zip(sym_rows["year"], sym_rows["ic"]))
                latest = sym_rows.iloc[-1]
                latest_year_grades[latest["grade"]] += 1

        regime_ic = {}
        if len(r_rows):
            for reg_name, reg_rows in r_rows.groupby("regime"):
                regime_ic[reg_name] = {
                    "mean_ic": float(reg_rows["ic"].mean()),
                    "n_symbols": int(reg_rows["symbol"].nunique()),
                    "pct_significant": float(reg_rows["significant"].mean() * 100),
                    "context": reg_rows["context"].iloc[0] if "context" in reg_rows.columns else None,
                }

        registry.append({
            "feature": name,
            "family": "SINGLE_ASSET_INDICATOR",
            "symbols_validated": symbols_validated,
            "symbols_decay": symbols_decay,
            "n_symbols_covered": len(symbols_validated) or len(symbols_decay),
            "n_symbols_total": len(discover_single_asset_symbols()),
            "mean_abs_ic": float(v_rows["ic_overall"].abs().mean()) if len(v_rows) else None,
            "n_delete": int((v_rows["recommendation"] == "DELETE").sum()) if len(v_rows) else 0,
            "n_review": int((v_rows["recommendation"] == "REVIEW").sum()) if len(v_rows) else 0,
            "n_keep": int(v_rows["recommendation"].isin(["KEEP", "STRONG KEEP"]).sum()) if len(v_rows) else 0,
            "n_passes_fdr": int(v_rows["passes_fdr"].sum()) if len(v_rows) else 0,
            "n_passes_bonferroni": int(v_rows["passes_bonferroni"].sum()) if len(v_rows) else 0,
            "stability_std_mean": float(v_rows["stability_std"].mean()) if len(v_rows) else None,
            "sharpe_contrib_mean": float(v_rows["sharpe_contrib"].mean()) if len(v_rows) else None,
            "trend_counts": dict(trend_counts),
            "latest_year_grades": dict(latest_year_grades),
            "yearly_ic_by_symbol": yearly_ic_by_symbol,
            "regime_ic": regime_ic,
            "validator_rows": v_rows.to_dict("records") if len(v_rows) else [],
            "decay_rows": d_rows.to_dict("records") if len(d_rows) else [],
            "correlation_available": True,
        })

    # ---- SINGLE_ASSET_INDICATOR: untested (computed, never validated) ----
    for name in get_untested_single_asset_features():
        registry.append({
            "feature": name,
            "family": "SINGLE_ASSET_INDICATOR",
            "symbols_validated": [],
            "symbols_decay": [],
            "n_symbols_covered": 0,
            "n_symbols_total": len(discover_single_asset_symbols()),
            "mean_abs_ic": None,
            "n_delete": 0, "n_review": 0, "n_keep": 0,
            "n_passes_fdr": 0, "n_passes_bonferroni": 0,
            "stability_std_mean": None,
            "sharpe_contrib_mean": None,
            "trend_counts": {},
            "latest_year_grades": {},
            "yearly_ic_by_symbol": {},
            "regime_ic": {},
            "validator_rows": [],
            "decay_rows": [],
            "correlation_available": True,
            "untested_reason": "Computed by feature_calculator.py but excluded from feature_validator.py's own candidate set (see VALIDATOR_EXCLUDE_COLS) and never picked up by feature_decay_analyzer.py.",
        })

    # ---- CROSS_SECTIONAL ----
    for _, row in institutional.iterrows():
        registry.append({
            "feature": row["feature"],
            "family": "CROSS_SECTIONAL",
            "symbols_validated": ["ALL (cross-sectional universe)"],
            "symbols_decay": [],
            "n_symbols_covered": 1,
            "n_symbols_total": 1,
            "mean_abs_ic": float(abs(row["ic_overall"])) if pd.notna(row.get("ic_overall")) else None,
            "n_delete": 1 if row.get("recommendation") == "DELETE" else 0,
            "n_review": 1 if row.get("recommendation") == "REVIEW" else 0,
            "n_keep": 1 if row.get("recommendation") in ("KEEP", "STRONG KEEP") else 0,
            "n_passes_fdr": int(bool(row.get("passes_fdr"))),
            "n_passes_bonferroni": int(bool(row.get("passes_bonferroni"))),
            "stability_std_mean": float(row["stability_std"]) if pd.notna(row.get("stability_std")) else None,
            "sharpe_contrib_mean": float(row["sharpe_contrib"]) if pd.notna(row.get("sharpe_contrib")) else None,
            "trend_counts": {},
            "latest_year_grades": {},
            "yearly_ic_by_symbol": {},
            "regime_ic": {},
            "validator_rows": [row.to_dict()],
            "decay_rows": [],
            "correlation_available": False,
        })

    # ---- PAIR_SPREAD ----
    for _, row in pair_stability.iterrows():
        pair_label = row["symbol"]  # e.g. "AVAX_ATOM_KALMAN"
        # claude code changed: new guard — some rows have a NaN "symbol"
        # (pandas reads a blank CSV cell as float('nan'), not ""), which
        # crashed .replace() below with AttributeError: 'float' object has
        # no attribute 'replace'. A row with no pair label isn't usable in
        # this registry anyway, so skip it instead of crashing the whole
        # feature-intelligence page.
        if not isinstance(pair_label, str):
            continue
        underlying_pair = pair_label.replace("_KALMAN", "").split("_")
        pair_folder_guess = None
        for folder in permutation_verdicts:
            if all(tok in folder for tok in underlying_pair):
                pair_folder_guess = folder
                break
        perm = permutation_verdicts.get(pair_folder_guess) if pair_folder_guess else None

        yearly_ic = {y: row.get(f"ic_{y}") for y in range(2020, 2026) if pd.notna(row.get(f"ic_{y}"))}

        registry.append({
            "feature": row["feature"],
            "family": "PAIR_SPREAD",
            "symbols_validated": [pair_label],
            "symbols_decay": [pair_label],
            "n_symbols_covered": 1,
            "n_symbols_total": 1,
            "mean_abs_ic": float(abs(row["ic_last_24m"])) if pd.notna(row.get("ic_last_24m")) else None,
            "n_delete": 0, "n_review": 0,
            "n_keep": 1 if str(row.get("verdict_last_24m", "")).endswith("KEEP") else 0,
            "n_passes_fdr": 0, "n_passes_bonferroni": 0,
            "stability_std_mean": None,
            "sharpe_contrib_mean": float(row["sharpe_last_24m"]) if pd.notna(row.get("sharpe_last_24m")) else None,
            "trend_counts": {row["trend"]: 1} if pd.notna(row.get("trend")) else {},
            "latest_year_grades": {row["verdict_last_24m"]: 1} if pd.notna(row.get("verdict_last_24m")) else {},
            "yearly_ic_by_symbol": {pair_label: yearly_ic},
            "regime_ic": {},
            "validator_rows": [],
            "decay_rows": [],
            "correlation_available": False,
            "pair_label": pair_label,
            "pair_folder": pair_folder_guess,
            "stability_verdict": row.get("verdict_last_24m"),
            "trend": row.get("trend"),
            "live_recommendation": row.get("live_recommendation"),
            "pair_permutation_edge_real": bool(perm["edge_appears_real"]) if perm else None,
        })

    # ---- LIVE_REGIME_INDICATOR ----
    for name in get_live_regime_feature_names():
        registry.append({
            "feature": name,
            "family": "LIVE_REGIME_INDICATOR",
            "symbols_validated": [],
            "symbols_decay": [],
            "n_symbols_covered": 0,
            "n_symbols_total": 0,
            "mean_abs_ic": None,
            "n_delete": 0, "n_review": 0, "n_keep": 0,
            "n_passes_fdr": 0, "n_passes_bonferroni": 0,
            "stability_std_mean": None,
            "sharpe_contrib_mean": None,
            "trend_counts": {},
            "latest_year_grades": {},
            "yearly_ic_by_symbol": {},
            "regime_ic": {},
            "validator_rows": [],
            "decay_rows": [],
            "correlation_available": False,
            "untested_reason": "Computed live by RegimeDetector.detect() for signal routing only (bot/engines/regime_detector.py) — never persisted to research_data/ or run through any validator.",
        })

    for agg in registry:
        status, rationale = classify_feature_status(agg)
        agg["status"] = status
        agg["status_rationale"] = rationale

        mean_yearly_ic = {}
        if agg["yearly_ic_by_symbol"]:
            years = sorted({y for series in agg["yearly_ic_by_symbol"].values() for y in series})
            for y in years:
                vals = [series[y] for series in agg["yearly_ic_by_symbol"].values() if y in series and pd.notna(series[y])]
                if vals:
                    mean_yearly_ic[y] = sum(vals) / len(vals)
        agg["mean_yearly_ic"] = mean_yearly_ic

    if use_cache:
        cache.set(_REGISTRY_CACHE_KEY, registry, 300)
    return registry


def classify_feature_status(agg):
    """
    Ordered cascade, first match wins. Every branch cites the exact real
    field(s) it read — see module docstring for the full taxonomy.
    """
    family = agg["family"]

    if family == "LIVE_REGIME_INDICATOR":
        return "UNTESTED", agg["untested_reason"]

    n_tested = len(agg["symbols_validated"]) or len(agg["symbols_decay"])
    if n_tested == 0:
        return "UNTESTED", agg.get("untested_reason", "No feature_validator.py or feature_decay_analyzer.py output found for this feature.")

    if family == "PAIR_SPREAD" and agg.get("pair_permutation_edge_real") is False:
        return "UNSTABLE", (
            f"feature_stability_analyzer.py verdict is {agg.get('stability_verdict')}, but "
            f"permutation_test_engine.py found the underlying {agg.get('pair_folder')} strategy's "
            f"edge does not survive 100-shuffle testing (edge_appears_real=False) — capped at UNSTABLE."
        )

    if family in ("SINGLE_ASSET_INDICATOR", "CROSS_SECTIONAL") and agg["n_delete"] and n_tested:
        delete_frac = agg["n_delete"] / n_tested
        if delete_frac >= 0.75:
            return "REJECTED", f"recommendation=DELETE on {agg['n_delete']}/{n_tested} tested symbols (feature_validator.py)."

    if family == "SINGLE_ASSET_INDICATOR" and agg["trend_counts"]:
        total_trend = sum(agg["trend_counts"].values())
        unstable = agg["trend_counts"].get("DECAYING", 0) + agg["trend_counts"].get("VOLATILE", 0)
        if total_trend and unstable / total_trend >= 0.5:
            return "UNSTABLE", f"feature_decay_analyzer.py classifies the IC trend as DECAYING/VOLATILE on {unstable}/{total_trend} tested symbols."

    if family == "PAIR_SPREAD" and str(agg.get("stability_verdict", "")).endswith("KEEP") and agg.get("trend") in ("STABLE", "GAINING"):
        return "STABLE", f"feature_stability_analyzer.py verdict={agg.get('stability_verdict')}, trend={agg.get('trend')}, live_recommendation={agg.get('live_recommendation')}."

    if family == "SINGLE_ASSET_INDICATOR" and agg["trend_counts"] and agg["latest_year_grades"]:
        total_trend = sum(agg["trend_counts"].values())
        stable_frac = (agg["trend_counts"].get("STABLE", 0) + agg["trend_counts"].get("GAINING", 0)) / total_trend
        total_grades = sum(agg["latest_year_grades"].values())
        dead_frac = (agg["latest_year_grades"].get("DEAD", 0) + agg["latest_year_grades"].get("NOT_SIG", 0)) / total_grades
        if stable_frac >= 0.5 and dead_frac < 0.5 and (agg["mean_abs_ic"] or 0) >= 0.02:
            return "STABLE", f"IC trend STABLE/GAINING on {stable_frac:.0%} of tested symbols, most-recent-year grade not DEAD/NOT_SIG on {(1 - dead_frac):.0%}, mean |IC|={agg['mean_abs_ic']:.4f} (feature_decay_analyzer.py)."

    if agg["n_passes_fdr"]:
        return "PROMISING", f"Passes FDR (Benjamini-Hochberg) correction on {agg['n_passes_fdr']}/{n_tested} symbols (feature_validator.py)."
    if family == "SINGLE_ASSET_INDICATOR" and (agg["trend_counts"].get("GAINING", 0) or agg["trend_counts"].get("EMERGING", 0)):
        return "PROMISING", "feature_decay_analyzer.py trend is GAINING/EMERGING on at least one symbol."
    if family == "PAIR_SPREAD" and str(agg.get("stability_verdict", "")) == "KEEP":
        return "PROMISING", f"feature_stability_analyzer.py verdict={agg.get('stability_verdict')}."

    if family in ("SINGLE_ASSET_INDICATOR", "CROSS_SECTIONAL") and agg["n_delete"] > 0 and (agg["n_review"] + agg["n_keep"]) > 0:
        return "REQUIRES_VALIDATION", f"recommendation is inconsistent across symbols — DELETE on {agg['n_delete']}, REVIEW/KEEP on {agg['n_review'] + agg['n_keep']} (feature_validator.py)."

    return "UNDER_RESEARCH", "Has validator/decay output but does not yet clear the bar for STABLE, PROMISING, or REJECTED — needs more evidence (e.g. a feature_stability_analyzer.py run for this symbol, or downstream strategy testing)."


# ============================================================
# CORRELATION / REDUNDANCY — the one metric that genuinely does not
# exist anywhere in the backend yet. This computes it live from real
# per-candle feature columns in {symbol}_observations.csv (a real
# derived statistic from real data, not a fabricated score), and caches
# the result since the source files are ~50k rows each.
# ============================================================
CORRELATION_FEATURE_COLUMNS = [
    "atr", "adr", "range_used", "efficiency", "atr_ratio", "realized_vol",
    "volume_ratio", "rsi", "ema_12", "ema_26", "adx", "atr_indicator",
    "bb_upper", "bb_middle", "bb_lower", "bb_width", "macd", "macd_signal",
    "macd_histogram",
]


# claude code changed: feature-correlation upgrade — units of every column,
# read off bot/research/feature_calculator.py's own formulas. Correlating
# raw price LEVELS (or dollar amounts that scale with price) measures the
# shared long-run price trend, not shared information — the textbook
# spurious correlation of non-stationary series. The "stationary" basis
# (default) puts every column in unitless, price-free terms first.
PRICE_LEVEL_COLUMNS = ("ema_12", "ema_26", "bb_upper", "bb_middle", "bb_lower")   # → level / close − 1 (distance from price)
PRICE_UNIT_COLUMNS = ("atr", "adr", "atr_indicator", "bb_width", "macd", "macd_signal", "macd_histogram")   # $ amounts → ÷ close
CORRELATION_BASES = ("stationary", "raw")
CORRELATION_METHODS = ("pearson", "spearman")
MIN_COLUMN_OBSERVATIONS = 100      # fewer non-null values than this → column reported as unavailable, not shown as nan
STABILITY_MIN_YEAR_OBS = 1000      # a calendar year needs this many paired candles to enter the per-year stability range

# claude code changed: pairs related BY CONSTRUCTION (feature_calculator.py's
# formulas) — a high correlation here is arithmetic, not a market finding.
_MAS = "moving averages of the same close"
DEFINITIONAL_RELATIONS = {
    frozenset(("atr", "atr_indicator")): "identical formula — _calculate_atr() is called twice",
    frozenset(("efficiency", "atr_ratio")): "atr_ratio = 1 / efficiency (each clipped)",
    frozenset(("adr", "range_used")): "range_used = (high − low) / adr",
    frozenset(("atr", "adr")): "both average the bar range (ATR 14 incl. gaps, ADR 20)",
    frozenset(("adr", "atr_indicator")): "both average the bar range (ATR 14 incl. gaps, ADR 20)",
    frozenset(("ema_12", "ema_26")): _MAS,
    frozenset(("ema_12", "bb_middle")): _MAS,
    frozenset(("ema_26", "bb_middle")): _MAS,
    frozenset(("bb_upper", "bb_middle")): "bands = middle ± 2σ",
    frozenset(("bb_lower", "bb_middle")): "bands = middle ± 2σ",
    frozenset(("bb_upper", "bb_lower")): "bands = middle ± 2σ",
    frozenset(("bb_width", "bb_upper")): "width = upper − lower",
    frozenset(("bb_width", "bb_lower")): "width = upper − lower",
    frozenset(("macd", "ema_12")): "macd = ema_12 − ema_26",
    frozenset(("macd", "ema_26")): "macd = ema_12 − ema_26",
    frozenset(("macd", "macd_signal")): "signal = EMA9(macd)",
    frozenset(("macd", "macd_histogram")): "histogram = macd − signal",
    frozenset(("macd_signal", "macd_histogram")): "histogram = macd − signal",
}


def _stationary_frame(df):   # claude code changed: new — feature-correlation upgrade
    """Price-free version of every column (see PRICE_LEVEL_COLUMNS /
    PRICE_UNIT_COLUMNS); unitless columns pass through unchanged.
    Returns (frame, {column: display label})."""
    close = df["close"].where(df["close"] > 0)
    out, labels = pd.DataFrame(index=df.index), {}
    for c in df.columns:
        if c in ("close", "timestamp"):
            continue
        if c in PRICE_LEVEL_COLUMNS:
            out[c], labels[c] = df[c] / close - 1.0, f"{c} ÷ close − 1"
        elif c in PRICE_UNIT_COLUMNS:
            out[c], labels[c] = df[c] / close, f"{c} ÷ close"
        else:
            out[c], labels[c] = df[c], c
    return out, labels


def _yearly_corr_range(x, y, years, method):   # claude code changed: new — feature-correlation upgrade
    """(min, max, n_years) of the pair's correlation within each calendar
    year holding >= STABILITY_MIN_YEAR_OBS paired candles; None if fewer
    than two such years (no stability claim possible)."""
    vals = []
    for _, idx in years.groupby(years).groups.items():
        xs, ys = x.loc[idx], y.loc[idx]
        ok = xs.notna() & ys.notna()
        if ok.sum() >= STABILITY_MIN_YEAR_OBS:
            v = xs[ok].corr(ys[ok], method=method)
            if pd.notna(v):
                vals.append(float(v))
    if len(vals) < 2:
        return None
    return {"min": min(vals), "max": max(vals), "n_years": len(vals), "sign_flip": min(vals) < 0 < max(vals)}


def get_feature_correlation_matrix(symbol, basis="stationary", method="pearson"):
    # claude code changed: feature-correlation upgrade — was raw Pearson over
    # every column as stored: price levels correlated through the shared
    # trend, an all-empty column (adx) filled the grid with nan, an exact
    # duplicate (atr_indicator) read as a finding, and a by-construction
    # pair (efficiency/atr_ratio) looked like market structure. Now:
    # stationary basis by default (raw still selectable), Spearman option,
    # empty/constant columns and exact duplicates reported separately,
    # every redundancy pair labelled BY CONSTRUCTION or EMPIRICAL with its
    # per-year stability range. Cache key includes the file's mtime, so a
    # regenerated observations file is picked up immediately.
    basis = basis if basis in CORRELATION_BASES else "stationary"
    method = method if method in CORRELATION_METHODS else "pearson"

    path = os.path.join(RESEARCH_DATA_DIR, f"{symbol}_observations.csv")
    if not os.path.exists(path):
        return {"available": False, "reason": f"{symbol}_observations.csv not found in research_data/"}
    mtime = os.path.getmtime(path)

    cache_key = f"feature_corr_matrix_v2_{symbol}_{basis}_{method}_{int(mtime)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = pd.read_csv(path, usecols=lambda c: c in CORRELATION_FEATURE_COLUMNS or c in ("close", "timestamp"))
    except Exception as e:
        return {"available": False, "reason": str(e)[:120]}

    present = [c for c in CORRELATION_FEATURE_COLUMNS if c in df.columns]
    if basis == "stationary" and "close" not in df.columns:
        return {"available": False, "reason": f"{symbol}_observations.csv has no close column — the stationary basis cannot be built"}
    if basis == "stationary":
        frame, labels = _stationary_frame(df[present + ["close"]])
    else:
        frame, labels = df[present].copy(), {c: c for c in present}

    unavailable, usable = [], []
    for c in present:
        n = int(frame[c].notna().sum())
        if n < MIN_COLUMN_OBSERVATIONS:
            unavailable.append({"feature": c, "reason": f"only {n:,} of {len(frame):,} values present"})
        elif frame[c].nunique(dropna=True) <= 1:
            unavailable.append({"feature": c, "reason": "constant — zero variance, correlation undefined"})
        else:
            usable.append(c)

    duplicates, cols = [], []
    for c in usable:
        twin = next((k for k in cols if frame[c].isna().equals(frame[k].isna())
                     and (frame[c] - frame[k]).abs().max() == 0), None)
        if twin is None:
            cols.append(c)
        else:
            duplicates.append({"feature": c, "duplicate_of": twin,
                               "why": DEFINITIONAL_RELATIONS.get(frozenset((c, twin)), "numerically identical in every row")})

    corr = frame[cols].corr(method=method)
    years = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dt.year if "timestamp" in df.columns else None

    redundant_pairs = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            val = corr.loc[a, b]
            if pd.notna(val) and abs(val) >= REDUNDANCY_THRESHOLD:
                why = DEFINITIONAL_RELATIONS.get(frozenset((a, b)))
                redundant_pairs.append({
                    "feature_a": a, "feature_b": b, "corr": float(val),
                    "kind": "BY CONSTRUCTION" if why else "EMPIRICAL", "why": why or "",
                    "yearly": _yearly_corr_range(frame[a], frame[b], years, method) if years is not None else None,
                })
    redundant_pairs.sort(key=lambda r: -abs(r["corr"]))

    result = {
        "available": True,
        "symbol": symbol,
        "basis": basis,
        "method": method,
        "features": cols,
        "labels": [labels[c] for c in cols],
        "matrix": corr.round(4).values.tolist(),
        "redundant_pairs": redundant_pairs,
        "unavailable": unavailable,
        "duplicates": duplicates,
        "threshold": REDUNDANCY_THRESHOLD,
        "n_candles": len(df),
        "first_timestamp": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns and len(df) else "",
        "last_timestamp": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else "",
        "file_date": pd.Timestamp(mtime, unit="s").strftime("%Y-%m-%d"),
    }
    cache.set(cache_key, result, 3600)
    return result


def get_top_correlated(feature_name, symbol, limit=5):
    """Used by the leaderboard/detail page to show a feature's strongest real correlations without rendering the full matrix."""
    matrix = get_feature_correlation_matrix(symbol)
    if not matrix.get("available") or feature_name not in matrix["features"]:
        return {"available": False}

    idx = matrix["features"].index(feature_name)
    pairs = [
        {"feature": other, "corr": matrix["matrix"][idx][j]}
        for j, other in enumerate(matrix["features"]) if other != feature_name
    ]
    pairs.sort(key=lambda p: -abs(p["corr"]))
    return {"available": True, "symbol": symbol, "top": pairs[:limit]}


# ============================================================
# PUBLIC PAGE-LEVEL FUNCTIONS
# ============================================================
def get_feature_leaderboard():
    registry = build_feature_registry()
    rows = sorted(registry, key=lambda a: (a["mean_abs_ic"] is None, -(a["mean_abs_ic"] or 0)))
    return {
        "available": True,
        "rows": rows,
        "n_features": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "family_counts": dict(Counter(r["family"] for r in rows)),
    }


def get_feature_detail(feature_name, correlation_symbol="BTC_USDT"):
    registry = build_feature_registry()
    agg = next((a for a in registry if a["feature"] == feature_name), None)
    if agg is None:
        return {"available": False, "reason": f"'{feature_name}' is not in the feature registry."}

    result = dict(agg)
    result["available"] = True

    if agg["correlation_available"]:
        result["correlation"] = get_top_correlated(feature_name, correlation_symbol)
        result["correlation_symbol"] = correlation_symbol
    else:
        result["correlation"] = {"available": False}

    return result


def get_feature_comparison(feature_names):
    registry = build_feature_registry()
    by_name = {a["feature"]: a for a in registry}
    rows = [by_name[n] for n in feature_names if n in by_name]
    missing = [n for n in feature_names if n not in by_name]
    return {"rows": rows, "missing": missing, "all_feature_names": sorted(by_name.keys())}


def get_research_status_board():
    leaderboard = get_feature_leaderboard()
    return {
        "status_counts": leaderboard["status_counts"],
        "n_features": leaderboard["n_features"],
        "family_counts": leaderboard["family_counts"],
    }
