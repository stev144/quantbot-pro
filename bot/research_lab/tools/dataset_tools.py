# ============================================================
# bot/research_lab/tools/dataset_tools.py
# Research Lab tools: inspect_dataset, calculate_feature (section 8).
#
# claude code changed: new file. Both wrap existing, already-tested
# modules — feature_calculator.py — never a second implementation of
# technical-indicator math (section 25).
# ============================================================

import numpy as np

from bot.research.feature_calculator import FeatureCalculator
from bot.research_lab.spec import DERIVABLE_FROM_OHLCV  # claude code changed: import from the single source (spec.py), not data_availability.py's former copy
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.base import register_tool


@register_tool("inspect_dataset")
def inspect_dataset(asset: str) -> dict:
    df = load_ohlcv(asset)
    return {
        "asset": asset,
        "n_observations": int(len(df)),
        "start": df.index.min().isoformat(),
        "end": df.index.max().isoformat(),
        "columns": list(df.columns),
    }


@register_tool("calculate_feature")
def calculate_feature(asset: str, feature_name: str) -> dict:
    if feature_name not in DERIVABLE_FROM_OHLCV:
        raise ValueError(f"'{feature_name}' is not a feature this tool can calculate")

    df = load_ohlcv(asset)
    calc = FeatureCalculator(min_data_required=100)
    enriched = calc.calculate_all_features(df, symbol=asset)

    if feature_name not in enriched.columns:
        raise ValueError(f"feature_calculator.py did not produce a '{feature_name}' column for this dataset")

    series = enriched[feature_name].dropna()
    return {
        "asset": asset,
        "feature_name": feature_name,
        "n_valid": int(len(series)),
        "mean": float(series.mean()) if len(series) else None,
        "std": float(series.std()) if len(series) else None,
        "min": float(series.min()) if len(series) else None,
        "max": float(series.max()) if len(series) else None,
        "n_nan": int(enriched[feature_name].isna().sum()),
    }
