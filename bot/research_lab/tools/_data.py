# claude code changed: new file — shared OHLCV loading for the tool
# layer, so every tool reads local CSVs the same way rather than each
# tool file inventing its own timestamp-parsing convention.

from pathlib import Path

import pandas as pd

from bot.fetch_all_symbols import symbol_to_filename

DATA_DIR = "data"


def load_ohlcv(asset: str) -> pd.DataFrame:
    """Reads the local, already-fetched OHLCV CSV for one symbol — never a
    live network call. Raises FileNotFoundError if the data isn't there;
    callers reach this only after the Data Availability Checker already
    confirmed it exists, so this is a defensive re-check, not the primary
    guard."""
    path = Path(DATA_DIR) / symbol_to_filename(asset)
    if not path.exists():
        raise FileNotFoundError(f"no OHLCV file at {path}")

    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["close"], inplace=True)
    df.sort_index(inplace=True)
    return df
