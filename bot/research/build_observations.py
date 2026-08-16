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

        # Add symbol column if it doesn't exist
        if "symbol" not in df.columns:

            # BTC_USDT_cross_section.csv
            # becomes
            # BTC_USDT
            symbol = file.stem.replace("_cross_section", "")

            df["symbol"] = symbol

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