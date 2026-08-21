# fetch_derivatives_data.py
#
# claude code changed: new — companion to fetch_all_symbols.py. Downloads
# funding rate + open interest history for the same 20 tracked symbols via
# bot/engines/derivatives_data.py, and saves to data/{SYMBOL}_funding.csv /
# data/{SYMBOL}_oi.csv, matching fetch_all_symbols.py's naming convention
# exactly (symbol.replace('/', '_') + suffix) so downstream code can find
# a symbol's files the same way it already finds {SYMBOL}_1h.csv.
#
# Real, load-bearing asymmetry between the two datasets (see
# derivatives_data.py's module docstring for the confirmed API limits):
#   - Funding rate: full history since 2020, same START_DATE as OHLCV.
#   - Open interest: Binance only serves ~30 days via REST, full stop.
#     Re-running this script later gets a different (newer) 30-day
#     window — it does not accumulate. A future step could append new OI
#     snapshots to build up real history over time; this script does not
#     attempt that, to avoid guessing at a persistence design the project
#     doesn't have a strong stated need for yet.
#
# Output: data/{SYMBOL}_funding.csv, data/{SYMBOL}_oi.csv

from pathlib import Path
from datetime import datetime, timezone

from bot.fetch_all_symbols import SYMBOLS
from bot.engines.derivatives_data import (
    fetch_funding_rate_history,
    fetch_open_interest_history,
    to_perp_symbol,
    MAX_OPEN_INTEREST_HISTORY_DAYS,
)

START_DATE = datetime(2020, 1, 1, tzinfo=timezone.utc)
OUTPUT_DIR = Path("data")


def symbol_to_funding_filename(symbol: str) -> str:
    return symbol.replace("/", "_") + "_funding.csv"


def symbol_to_oi_filename(symbol: str) -> str:
    return symbol.replace("/", "_") + "_oi.csv"


def download_all_derivatives_data() -> dict:
    """
    Download funding rate + open interest history for every symbol in
    fetch_all_symbols.SYMBOLS. Returns a summary dict per symbol.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    since_ms = int(START_DATE.timestamp() * 1000)

    print("\n" + "=" * 80)
    print(f"DOWNLOADING FUNDING RATE + OPEN INTEREST FOR {len(SYMBOLS)} SYMBOLS")
    print(f"Funding rate : {START_DATE.date()} -> today (full history)")
    print(f"Open interest: last {MAX_OPEN_INTEREST_HISTORY_DAYS} days (Binance's real API limit)")
    print("=" * 80)

    summary = {}

    for symbol in SYMBOLS:
        print(f"\n[Downloading] {symbol}")
        entry = {
            "funding_rows": 0, "funding_file": None,
            "oi_rows": 0, "oi_file": None,
            "error": None,
        }

        perp_symbol = to_perp_symbol(symbol)
        if perp_symbol is None:
            entry["error"] = "no Binance USDT-margined perpetual for this symbol"
            print(f"  x Skipped: {entry['error']}")
            summary[symbol] = entry
            continue

        try:
            funding_df = fetch_funding_rate_history(symbol, since_ms=since_ms)
            if not funding_df.empty:
                funding_path = OUTPUT_DIR / symbol_to_funding_filename(symbol)
                funding_df.to_csv(funding_path, index=False)
                entry["funding_rows"] = len(funding_df)
                entry["funding_file"] = str(funding_path)
                print(f"  + Funding rate : {len(funding_df):,} rows -> {funding_path}")
            else:
                print("  x Funding rate : no data returned")
        except Exception as e:
            print(f"  x Funding rate fetch failed: {e}")
            entry["error"] = f"funding: {e}"

        try:
            oi_df = fetch_open_interest_history(symbol, days=MAX_OPEN_INTEREST_HISTORY_DAYS)
            if not oi_df.empty:
                oi_path = OUTPUT_DIR / symbol_to_oi_filename(symbol)
                oi_df.to_csv(oi_path, index=False)
                entry["oi_rows"] = len(oi_df)
                entry["oi_file"] = str(oi_path)
                print(f"  + Open interest: {len(oi_df):,} rows -> {oi_path}")
            else:
                print("  x Open interest: no data returned")
        except Exception as e:
            print(f"  x Open interest fetch failed: {e}")
            entry["error"] = ((entry["error"] + "; ") if entry["error"] else "") + f"oi: {e}"

        summary[symbol] = entry

    print("\n" + "=" * 80)
    print("DOWNLOAD COMPLETE")
    print("=" * 80)
    ok_funding = sum(1 for v in summary.values() if v["funding_rows"] > 0)
    ok_oi = sum(1 for v in summary.values() if v["oi_rows"] > 0)
    print(f"\nFunding rate: {ok_funding}/{len(SYMBOLS)} symbols downloaded")
    print(f"Open interest: {ok_oi}/{len(SYMBOLS)} symbols downloaded\n")

    return summary


if __name__ == "__main__":
    download_all_derivatives_data()
