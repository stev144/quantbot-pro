"""
bot/convert_mt5_export.py
Convert MT5 History Center CSV exports to the project's standard format.

MT5 exports bars in server-clock time (NY+7, i.e. UTC+2 in summer / UTC+3 in winter).
This script converts them to true UTC, strips the v7 holdout window, writes provenance
markers, and saves to data/forex/{SYMBOL}_{TIMEFRAME}.csv.

Usage (from the project root, with venv active):
  python bot/convert_mt5_export.py EURUSD_M5.csv  EUR/USD  5m
  python bot/convert_mt5_export.py EURUSD_M15.csv EUR/USD  15m
  python bot/convert_mt5_export.py GBPUSD_M5.csv  GBP/USD  5m
  python bot/convert_mt5_export.py GBPUSD_M15.csv GBP/USD  15m
  python bot/convert_mt5_export.py USDCHF_M5.csv  USD/CHF  5m
  python bot/convert_mt5_export.py USDCHF_M15.csv USD/CHF  15m
  python bot/convert_mt5_export.py AUDUSD_M5.csv  AUD/USD  5m
  python bot/convert_mt5_export.py AUDUSD_M15.csv AUD/USD  15m

The input file path can be absolute or relative to the current directory.

MT5 export format (typical):
  <DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>
  or
  <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>
  (format varies by MT5 version — this script handles both)
"""
# claude code changed: new file — v8 intraday; convert MT5 History Center exports to project format

import argparse
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QUANTBOT_SECRET_KEY", "dummy-key-for-offline-script")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

import pandas as pd
from bot.mt5_data_fetcher import mt5_server_time_to_utc, MT5_CLOCK_LABEL
from bot.quant_core.research_zones import seal_before_write
from bot.instruments import write_provenance_marker

OUTPUT_DIR = Path("data/forex")


def _detect_separator(path: Path) -> str:
    """Detect whether the MT5 export uses tab or comma as separator."""  # claude code changed: new
    with open(path, encoding="utf-8", errors="replace") as f:
        first_line = f.readline()
    return "\t" if "\t" in first_line else ","


def load_mt5_export(path: Path) -> pd.DataFrame:  # claude code changed: new
    """
    Load an MT5 History Center CSV export and return a clean DataFrame
    with UTC-aware timestamps.

    Handles both the tab-delimited and comma-delimited formats MT5 produces,
    and the two common column layouts:
      - Date + Time as separate columns (most common)
      - <Date Time> as a single combined column
    """
    sep = _detect_separator(path)
    df = pd.read_csv(path, sep=sep, header=0, encoding="utf-8", errors="replace")

    # Normalise column names: strip whitespace and angle brackets
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]

    # Build a combined datetime string from Date+Time or a single datetime col
    if "date" in df.columns and "time" in df.columns:
        dt_str = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
    elif "datetime" in df.columns:
        dt_str = df["datetime"].astype(str).str.strip()
    else:
        # Fall back: assume first two columns are date and time
        dt_str = df.iloc[:, 0].astype(str).str.strip() + " " + df.iloc[:, 1].astype(str).str.strip()

    # Parse as naive datetime (MT5 server clock, not UTC)
    naive_dt = pd.to_datetime(dt_str, dayfirst=False, errors="coerce")

    # Convert from MT5 server clock (NY+7) to true UTC
    df["timestamp"] = mt5_server_time_to_utc(naive_dt).to_numpy()

    # Normalise OHLCV columns (handle tickvol / vol / spread extra columns)
    rename_map = {}
    for col in df.columns:
        if col in ("open", "high", "low", "close"):
            rename_map[col] = col
        elif col in ("tickvol", "tick_volume", "volume", "vol"):
            rename_map[col] = "volume"
    df = df.rename(columns=rename_map)

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = 0.0

    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df.dropna(subset=["timestamp", "open", "high", "low", "close"], inplace=True)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def convert(input_path: str, pair: str, interval: str) -> None:  # claude code changed: new
    """Full pipeline: load → UTC-convert → seal → save → provenance."""
    src = Path(input_path)
    if not src.exists():
        print(f"ERROR: file not found: {src}")
        sys.exit(1)

    print(f"Loading {src.name} …")
    df = load_mt5_export(src)
    print(f"  Loaded {len(df):,} bars  ({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]})")

    output_name = pair.replace("/", "_") + f"_{interval}.csv"
    output_path = OUTPUT_DIR / output_name
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Applying v7 holdout seal …")
    df = seal_before_write(df, output_path)
    print(f"  {len(df):,} bars after seal (rows after 2026-10-16 08:00 UTC stripped)")

    df.to_csv(output_path, index=False)
    print(f"Saved → {output_path}")

    write_provenance_marker(
        output_path, venue="mt5", data_source="convert_mt5_export",
        source="mt5_history_center_export", symbol=pair, timeframe=interval,
        start_date=str(df["timestamp"].iloc[0].date()),
        end_date=str(df["timestamp"].iloc[-1].date()),
        row_count=len(df), clock=MT5_CLOCK_LABEL,
    )
    print(f"Provenance marker written.")
    print(f"Done: {pair} {interval}  ({len(df):,} bars)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert MT5 History Center export to project data/forex/ format"
    )
    parser.add_argument("input", help="Path to the MT5-exported CSV file")
    parser.add_argument("pair", help="Canonical pair name, e.g. EUR/USD")
    parser.add_argument("interval", help="Timeframe string, e.g. 5m or 15m")
    args = parser.parse_args()
    convert(args.input, args.pair, args.interval)
