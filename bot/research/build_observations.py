# =====================================================================================
# FILE: build_observations.py
#
# PURPOSE:
# Reads every *_cross_section.csv file produced by the
# CrossSectionEngine and combines them into one observations.csv
#
# This file sits between:
#
# CrossSectionEngine
#          ↓
# build_observations.py
#          ↓
# observations.csv
#          ↓
# Feature Validator
#
# =====================================================================================

# Import pathlib for working with folders and file paths
from pathlib import Path

# Import pandas for reading and combining CSV files
import pandas as pd

# Import logging so we can see what is happening
import logging

# claude code changed: new — real bug fix. observations.csv never carried
# 'realized_vol'/'adx', so feature_validator.py's MarketRegimeDetector
# (which unconditionally needs them) raised a KeyError on every single
# real run of `python -m bot.research.feature_validator`, silently caught
# by its own blanket except and defaulting every observation to "ranging"
# — not an edge case, guaranteed on this file's current column set.
# FeatureCalculator is the ONE canonical place this project computes
# realized_vol/adx (same instance every correct caller — run_research_
# all.py, bot/research_lab/tools/statistical_tools.py — already uses);
# reusing it here rather than re-deriving these formulas a second time.
from bot.research.feature_calculator import FeatureCalculator


# -----------------------------------------------------------------------------
# Configure logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Main Function
# -----------------------------------------------------------------------------

def build_observations():

    # Folder containing the CSV files
    research_folder = Path("research_data")

    # Find every CSV ending with "_cross_section.csv"
    csv_files = sorted(
        research_folder.glob("*_cross_section.csv")
    )

    # If nothing was found, stop here
    if len(csv_files) == 0:
        logger.error("No cross section files found.")
        return

    logger.info("=" * 70)
    logger.info("BUILDING observations.csv")
    logger.info("=" * 70)

    # Store every dataframe here
    frames = []

    # -------------------------------------------------------------------------
    # Read each CSV
    # -------------------------------------------------------------------------

    for file in csv_files:

        logger.info(f"Reading {file.name}")

        # Load the CSV
        df = pd.read_csv(file)

        # BTC_USDT_cross_section.csv
        # becomes
        # BTC_USDT
        symbol = file.stem.replace("_cross_section", "")

        # Add symbol column if it doesn't exist
        if "symbol" not in df.columns:
            df["symbol"] = symbol

        # claude code changed: new — compute realized_vol/adx for this
        # symbol via the canonical FeatureCalculator (see this module's
        # import comment for why), then merge just those two columns in.
        # cross_section.csv already carries open/high/low/close/volume
        # (CrossSectionEngine's own input), so no extra data source is
        # needed. Merged by position (both frames share this file's own
        # row order — cross_section.csv is one symbol's own chronological
        # series, never resorted) rather than by timestamp, so a duplicate
        # or slightly-mismatched timestamp format can never silently
        # produce a many-to-many join.
        required_ohlcv = {"open", "high", "low", "close", "volume"}
        if required_ohlcv.issubset(df.columns):
            try:
                calc = FeatureCalculator(min_data_required=100)
                enriched = calc.calculate_all_features(
                    df[["open", "high", "low", "close", "volume"]].copy(),
                    symbol=symbol, timeframe="1h",
                )
                # claude code changed: on internal failure (insufficient
                # rows, or its own caught exception) calculate_all_features()
                # returns the ORIGINAL frame unchanged — same length, but
                # without realized_vol/adx. Check for the columns explicitly
                # rather than assuming length-match implies success.
                if len(enriched) == len(df) and {"realized_vol", "adx"}.issubset(enriched.columns):
                    df["realized_vol"] = enriched["realized_vol"].to_numpy()
                    df["adx"] = enriched["adx"].to_numpy()
                else:
                    logger.warning(
                        f"  {symbol}: FeatureCalculator did not produce realized_vol/adx "
                        f"(rows: {len(enriched)} vs {len(df)}) — skipping for this symbol"
                    )
            except Exception as e:
                logger.warning(f"  {symbol}: failed to compute realized_vol/adx: {e}")
        else:
            logger.warning(
                f"  {symbol}: missing OHLCV columns "
                f"({required_ohlcv - set(df.columns)}) — skipping realized_vol/adx"
            )

        logger.info(f"Loaded {len(df):,} rows")

        frames.append(df)

    # -------------------------------------------------------------------------
    # Merge every dataframe vertically
    # -------------------------------------------------------------------------

    logger.info("Combining datasets...")

    observations = pd.concat(
        frames,
        ignore_index=True
    )

    logger.info(f"Total observations: {len(observations):,}")

    # -------------------------------------------------------------------------
    # Sort by timestamp if available
    # -------------------------------------------------------------------------

    if "timestamp" in observations.columns:

        observations = observations.sort_values(
            "timestamp"
        )

        observations = observations.reset_index(
            drop=True
        )

    # -------------------------------------------------------------------------
    # Save
    # -------------------------------------------------------------------------

    output = research_folder / "observations.csv"

    observations.to_csv(
        output,
        index=False
    )

    logger.info("=" * 70)
    logger.info(f"Saved {output}")
    logger.info(f"Rows : {len(observations):,}")
    logger.info(f"Columns : {len(observations.columns)}")
    logger.info("=" * 70)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":

    build_observations()