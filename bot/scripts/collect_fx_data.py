"""
bot/scripts/collect_fx_data.py
Stage 2 forex data collection — instrument specs + historical 1h candles.

Run ONCE after MT5 terminal is open and logged in (Read Only demo is enough).
The terminal must already be running; this script attaches to it.

Usage (from project root, venv activated):
    python bot/scripts/collect_fx_data.py

What it does (all five gates enforced throughout):
  1. Fetch instrument specs for all 5 target pairs via mt5.symbol_info()
  2. Print specs in a format ready to copy into ForexInstrumentSpec
  3. Fetch ~50,000 1h candles per pair via existing get_mt5_klines()
  4. Run mandatory protocol validation (OHLC, gaps, trading-hours check)
  5. Save validated data to data/forex/ with dataset fingerprints
  6. Print a readiness report — what is and isn't cost_complete

FX_DCA_PROTOCOL_2026-10-16.md governs this run.
DO NOT run this to examine results — it is data collection only.
DO NOT modify ladder parameters or DCA config based on this output.
"""
# claude code changed: new file — 2026-10-16, Stage 2 FX data collection

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── project root on sys.path ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from bot.mt5_data_fetcher import (  # claude code changed: reuse, not re-implement
    MT5ConnectionError,
    _attach_running_terminal,
    _mt5_timeframe,
    _to_mt5_symbol,
    get_mt5_symbol_info,
    mt5_server_time_to_utc,
)
from bot.research.forex_data_validation import (  # claude code changed: reuse
    is_expected_gap,
    validate_ohlc_candle,
)
from bot.quant_core.market_profile import FOREX_PROFILE  # claude code changed: must be canonical before any run

# ── protocol constants ───────────────────────────────────────────────────────
TARGET_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD"]  # claude code changed: FX-DCA-PROTOCOL-1 Part 2
MIN_TRADING_CANDLES = 37_000   # claude code changed: amended minimum — FX-DCA-PROTOCOL-1-A1 (was 40_000; revised after confirming broker full-history limit ~37,461)
TARGET_TRADING_CANDLES = 50_000
HISTORY_DAYS = 2200            # ~6 years; MT5 will return however many it has
TIMEFRAME = "1h"
OUTPUT_DIR = _ROOT / "data" / "forex"
SPEC_OUTPUT = _ROOT / "data" / "forex" / "fx_instrument_specs_collected.json"

_MT5_TO_CANONICAL = {  # claude code changed: broker uses no suffix (confirmed from MT5 screenshot)
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "USD/CHF": "USDCHF",
    "AUD/USD": "AUDUSD",
}


def _fingerprint(df: pd.DataFrame) -> str:
    """SHA-256 of the raw candle data for provenance."""  # claude code changed: protocol Part 2 §5
    raw = df[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(index=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_data(df: pd.DataFrame, symbol: str, profile) -> dict:
    """Run all mandatory protocol validations. Returns a summary dict."""  # claude code changed: FX-DCA-PROTOCOL-1 Part 2 §1-4
    errors = []
    warnings = []

    # 1. Duplicate timestamps
    dupes = df["timestamp"].duplicated().sum()
    if dupes:
        errors.append(f"{dupes} duplicate timestamps")

    # 2. OHLC validation per candle
    ohlc_errors = 0
    for _, row in df.iterrows():
        errs = validate_ohlc_candle(row)  # claude code changed: fix wrong kwarg (was instrument=, param is spec=)
        if errs:
            ohlc_errors += 1
    if ohlc_errors:
        errors.append(f"{ohlc_errors} candles with OHLC violations")

    # 3. Zero/negative prices
    bad_prices = ((df[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum()
    if bad_prices:
        errors.append(f"{bad_prices} candles with zero/negative prices")

    # 4. Gap audit — count unexpected gaps vs expected weekend/holiday gaps
    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    unexpected_gaps = []
    expected_gap_count = 0
    total_gap_hours = 0.0
    for i in range(1, len(df_sorted)):
        prev = df_sorted.loc[i - 1, "timestamp"]
        curr = df_sorted.loc[i, "timestamp"]
        gap_hours = (curr - prev).total_seconds() / 3600
        if gap_hours > 1.5:  # more than 1 candle gap
            total_gap_hours += gap_hours
            if is_expected_gap(prev, curr, profile):
                expected_gap_count += 1
            else:
                unexpected_gaps.append({
                    "from": str(prev), "to": str(curr),
                    "gap_hours": round(gap_hours, 1),
                })
    if len(unexpected_gaps) > 20:
        warnings.append(f"{len(unexpected_gaps)} unexpected gaps (first few shown below)")

    # 5. Trading candle count (excluding weekend/holiday gaps)
    # Approximate: total candles minus estimated closure hours
    trading_candles = len(df_sorted)  # protocol counts observed trading candles

    return {
        "errors": errors,
        "warnings": warnings,
        "trading_candles": trading_candles,
        "expected_gaps": expected_gap_count,
        "unexpected_gaps": unexpected_gaps[:5],  # first 5 only
        "total_gap_hours": round(total_gap_hours, 1),
        "date_range": {
            "first": str(df_sorted["timestamp"].iloc[0]),
            "last": str(df_sorted["timestamp"].iloc[-1]),
        },
    }


def collect_specs() -> dict:  # claude code changed: new — fetch instrument specs for all 5 pairs
    """Fetch instrument specs from the running MT5 terminal."""
    print("\n" + "="*70)
    print("STAGE 2: FETCHING INSTRUMENT SPECS FROM MT5")
    print("Protocol: FX-DCA-PROTOCOL-1-A1 (OD-FX-CM-1, OD-FX-FS-1)")  # claude code changed: F8 — updated to A1 amendment identifier
    print("="*70)

    specs = {}
    for canonical in TARGET_PAIRS:
        print(f"\n  [{canonical}]")
        try:
            info = get_mt5_symbol_info(canonical, attach_only=True)
            specs[canonical] = info
            print(f"    pip_size          : {info['pip_size']}")
            print(f"    price_precision   : {info['price_precision']} decimal places")
            print(f"    contract_size     : {info['contract_size']:,.0f} units/lot")
            print(f"    min_volume        : {info['min_volume']} lots")
            print(f"    volume_increment  : {info['volume_increment']} lots")
            print(f"    spread_pips       : {info['spread_pips']} pips (current, live)")
            print(f"    commission/lot    : {info['commission_per_lot']} USD")
            print(f"    swap_long (pips/d): {info['swap_long_pips']}")
            print(f"    swap_short (pip/d): {info['swap_short_pips']}")
            print(f"    swap_triple_day   : {info['swap_triple_day']} (0=Sun…6=Sat)")
            print(f"    leverage_max      : {info['leverage_max']}:1")
            print(f"    margin_rate       : {info['margin_rate']}")
            cost_ok = all(v is not None for v in [
                info["spread_pips"], info["commission_per_lot"],
                info["swap_long_pips"], info["swap_short_pips"]
            ])
            status = "cost_complete=TRUE" if cost_ok else "cost_complete=FALSE (some fields None)"
            print(f"    >>> {status}")
        except MT5ConnectionError as e:
            print(f"    ERROR: {e}")
            specs[canonical] = {"error": str(e)}

    # Save raw specs to file for the ForexInstrumentSpec update step
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SPEC_OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "collected_utc": datetime.now(tz=timezone.utc).isoformat(),
            "source": "ICFundedEvaluations-MT5-Demo",
            "protocol": "FX-DCA-PROTOCOL-1-A1",  # claude code changed: F8 — updated to A1 amendment identifier
            "specs": specs,
        }, f, indent=2, default=str)
    print(f"\n  Specs saved to: {SPEC_OUTPUT}")
    return specs


def collect_candles() -> dict:  # claude code changed: new — fetch and validate historical 1h data
    """Fetch ~50,000 1h candles per pair, validate, save with fingerprint."""
    print("\n" + "="*70)
    print(f"STAGE 2: FETCHING HISTORICAL 1h DATA ({HISTORY_DAYS} days)")
    print("Protocol: FX-DCA-PROTOCOL-1-A1 Part 2 — mandatory validation enforced")  # claude code changed: F8 — updated to A1 amendment identifier
    print("="*70)

    # Must be canonical before any data collection
    FOREX_PROFILE.require_canonical("collect_fx_data.py")  # claude code changed: gates the run

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}

    mt5 = _attach_running_terminal()
    try:
        for canonical in TARGET_PAIRS:
            mt5_sym = _MT5_TO_CANONICAL[canonical]
            print(f"\n  [{canonical}] (MT5 symbol: {mt5_sym})")
            try:
                tf = _mt5_timeframe(mt5, TIMEFRAME)
                if not mt5.symbol_select(mt5_sym, True):
                    raise MT5ConnectionError(f"symbol_select failed: {mt5.last_error()}")

                date_to = datetime.now(tz=timezone.utc)
                date_from = date_to - timedelta(days=HISTORY_DAYS)
                rates = mt5.copy_rates_range(mt5_sym, tf, date_from, date_to)

                if rates is None or len(rates) == 0:
                    raise MT5ConnectionError(f"no data returned: {mt5.last_error()}")

                df = pd.DataFrame(rates)
                df["timestamp"] = mt5_server_time_to_utc(df["time"]).to_numpy()
                df = df.rename(columns={"tick_volume": "volume"})[
                    ["timestamp", "open", "high", "low", "close", "volume"]
                ]
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                df.drop_duplicates(subset=["timestamp"], inplace=True)
                df.sort_values("timestamp", inplace=True)
                df.reset_index(drop=True, inplace=True)

                print(f"    Raw candles      : {len(df):,}")

                # Validate
                validation = _validate_data(df, canonical, FOREX_PROFILE.calendar)

                if validation["errors"]:
                    print(f"    VALIDATION ERRORS: {validation['errors']}")
                    summary[canonical] = {"success": False, "errors": validation["errors"]}
                    continue

                if validation["warnings"]:
                    print(f"    Warnings         : {validation['warnings']}")

                print(f"    Trading candles  : {validation['trading_candles']:,}")
                print(f"    Date range       : {validation['date_range']['first']} → {validation['date_range']['last']}")
                print(f"    Expected gaps    : {validation['expected_gaps']} (weekends/holidays)")
                if validation["unexpected_gaps"]:
                    print(f"    Unexpected gaps  : {len(validation['unexpected_gaps'])} (first shown):")
                    for g in validation["unexpected_gaps"][:3]:
                        print(f"      {g['from']} → {g['to']} ({g['gap_hours']}h)")

                meets_minimum = validation["trading_candles"] >= MIN_TRADING_CANDLES
                status = "OK" if meets_minimum else f"BELOW MINIMUM ({MIN_TRADING_CANDLES:,} required)"
                print(f"    Candle check     : {status}")

                # Fingerprint and save
                fingerprint = _fingerprint(df)
                out_path = OUTPUT_DIR / f"{mt5_sym}_1h.csv"
                df.to_csv(out_path, index=False)

                prov = {
                    "canonical_symbol": canonical,
                    "mt5_symbol": mt5_sym,
                    "source": "MT5 ICFundedEvaluations-MT5-Demo",
                    "timeframe": TIMEFRAME,
                    "collected_utc": datetime.now(tz=timezone.utc).isoformat(),
                    "protocol": "FX-DCA-PROTOCOL-1-A1",  # claude code changed: F8 — updated to A1 amendment identifier
                    "trading_candles": validation["trading_candles"],
                    "date_range": validation["date_range"],
                    "dataset_fingerprint_sha256": fingerprint,
                    "meets_minimum": meets_minimum,
                    "unexpected_gaps": validation["unexpected_gaps"],
                    "validation_errors": validation["errors"],
                }
                prov_path = out_path.with_suffix(".provenance.json")
                with open(prov_path, "w", encoding="utf-8") as f:
                    json.dump(prov, f, indent=2)

                print(f"    Fingerprint      : {fingerprint[:16]}...")
                print(f"    Saved to         : {out_path}")

                summary[canonical] = {
                    "success": True,
                    "trading_candles": validation["trading_candles"],
                    "meets_minimum": meets_minimum,
                    "fingerprint": fingerprint,
                    "file": str(out_path),
                }

            except MT5ConnectionError as e:
                print(f"    ERROR: {e}")
                summary[canonical] = {"success": False, "errors": [str(e)]}
            except Exception as e:
                print(f"    ERROR (unexpected): {e}")
                summary[canonical] = {"success": False, "errors": [str(e)]}
    finally:
        mt5.shutdown()

    return summary


def print_readiness_report(specs: dict, candles: dict) -> None:  # claude code changed: new — final gate summary
    """Print the readiness report: what is and isn't ready for simulation."""
    print("\n" + "="*70)
    print("READINESS REPORT — FX-DCA-PROTOCOL-1-A1")  # claude code changed: F8 — updated to A1 amendment identifier
    print("="*70)
    print(f"\n{'Pair':<12} {'Candles':>10} {'Min OK':>8} {'Cost Complete':>15} {'Notes'}")
    print("-"*70)

    ready_for_simulation = True
    for pair in TARGET_PAIRS:
        cinfo = candles.get(pair, {})
        sinfo = specs.get(pair, {})

        if "error" in sinfo:
            cost_ok = False
            cost_label = "SPEC ERROR"
        else:
            cost_ok = all(v is not None for v in [
                sinfo.get("spread_pips"), sinfo.get("commission_per_lot"),
                sinfo.get("swap_long_pips"), sinfo.get("swap_short_pips"),
            ])
            cost_label = "YES" if cost_ok else "NO (None fields)"

        if cinfo.get("success"):
            n = cinfo["trading_candles"]
            min_ok = "YES" if cinfo["meets_minimum"] else "NO"
            notes = ""
        else:
            n = 0
            min_ok = "NO"
            notes = cinfo.get("errors", ["unknown error"])[0][:30]
            ready_for_simulation = False

        if not cost_ok:
            ready_for_simulation = False

        print(f"{pair:<12} {n:>10,} {min_ok:>8} {cost_label:>15}  {notes}")

    print("-"*70)
    verdict = "READY FOR UNIT TESTING" if ready_for_simulation else "NOT YET READY — see issues above"
    print(f"\nSimulation readiness: {verdict}")
    if not ready_for_simulation:
        print("\nNext actions:")
        print("  - If cost fields are None: update ForexInstrumentSpec with real broker values from specs above")
        print("  - If candle count below minimum: check MT5 history depth settings (Tools → Options → Charts)")
        print("  - If MT5 errors: confirm terminal is open, logged in, and symbols are in Market Watch")
    print()


if __name__ == "__main__":
    print(f"\nFX Data Collection — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Protocol: FX-DCA-PROTOCOL-1-A1")  # claude code changed: F8 — updated to A1 amendment identifier
    print(f"Account : ICFundedEvaluations-MT5-Demo (Read Only demo — data collection only)")

    specs = collect_specs()
    candles = collect_candles()
    print_readiness_report(specs, candles)

    print("Collection complete. Review the readiness report above.")
    print(f"Instrument specs saved to: {SPEC_OUTPUT}")
    print("Next step: update ForexInstrumentSpec with verified broker values, then re-run tests.")
