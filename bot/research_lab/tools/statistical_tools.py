# ============================================================
# bot/research_lab/tools/statistical_tools.py
# Research Lab tools: run_statistical_test, run_fdr_correction (section 8).
#
# claude code changed: new file. Both wrap bot/research/feature_validator.py
# directly — the most rigorously audited module in the repo — never a
# second IC/p-value implementation (section 25).
# ============================================================

from bot.research.feature_calculator import FeatureCalculator
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.research_lab.spec import SUPPORTED_HORIZONS  # claude code changed: moved to spec.py — single source, was independently defined here and could drift from validate_spec()'s check (Bug 4)
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.base import register_tool


def _prepare(asset: str, horizon: int):
    if horizon not in SUPPORTED_HORIZONS:
        raise ValueError(f"horizon={horizon} is not supported — only {sorted(SUPPORTED_HORIZONS)} candles are available as labels")
    df = load_ohlcv(asset)
    calc = FeatureCalculator(min_data_required=100)
    enriched = calc.calculate_all_features(df, symbol=asset)
    return enriched, SUPPORTED_HORIZONS[horizon]


@register_tool("run_statistical_test")
def run_statistical_test(asset: str, feature_name: str, horizon: int, random_seed: int = 42) -> dict:
    enriched, forward_col = _prepare(asset, horizon)
    if feature_name not in enriched.columns:
        raise ValueError(f"'{feature_name}' is not a column feature_calculator.py produced")

    single_feature_df = enriched[[feature_name, forward_col]].copy()  # claude code changed: scope the validator to exactly the one feature under test — this call's own hypothesis family is size 1
    validator = FeatureValidator(random_seed=random_seed)
    raw_results = validator.validate_all_features_raw(single_feature_df, forward_return_col=forward_col)

    if raw_results.empty:
        raise ValueError("feature_validator.py produced no result for this feature/asset/horizon combination")

    row = raw_results.iloc[0]  # claude code changed: real column names confirmed by inspecting validate_all_features_raw()'s actual output, not the InstitutionalValidationResult dataclass field names (they differ)
    return {
        "asset": asset, "feature_name": feature_name, "horizon": horizon,
        "metric": "information_coefficient",
        "ic": float(row["ic_overall"]),
        "ic_rank": str(row["ic_rank"]),
        "p_value": float(row["p_value_mannwhitney"]),
        "block_permutation_p_value": float(row["p_value_block_permutation"]),
        "sample_size": int(len(single_feature_df.dropna())),
        "random_seed": random_seed,
    }


@register_tool("run_fdr_correction")
def run_fdr_correction(asset: str, feature_name: str, horizon: int, random_seed: int = 42) -> dict:
    """
    Applies the same family-wide FDR correction run_research_all.py uses
    across the full symbol universe, scoped here to this one experiment's
    single (asset, feature) hypothesis family — a legitimate, correct use
    of apply_family_wide_correction()'s real contract (it's generic over
    however many symbols/features are passed, not hardcoded to many).
    """
    enriched, forward_col = _prepare(asset, horizon)
    if feature_name not in enriched.columns:
        raise ValueError(f"'{feature_name}' is not a column feature_calculator.py produced")

    single_feature_df = enriched[[feature_name, forward_col]].copy()
    validator = FeatureValidator(random_seed=random_seed)
    raw_results = validator.validate_all_features_raw(single_feature_df, forward_return_col=forward_col)

    corrected_by_symbol, pooled = apply_family_wide_correction({asset: raw_results}, alpha=0.05)
    corrected = corrected_by_symbol[asset].iloc[0]  # claude code changed: real column names confirmed by inspecting apply_family_wide_correction()'s actual output

    return {
        "asset": asset, "feature_name": feature_name, "horizon": horizon,
        "raw_p_value": float(raw_results.iloc[0]["p_value_mannwhitney"]),
        "fdr_adjusted_p_value": float(corrected["p_fdr"]),
        "passes_fdr": bool(corrected["passes_fdr"]),
        "random_seed": random_seed,
    }
