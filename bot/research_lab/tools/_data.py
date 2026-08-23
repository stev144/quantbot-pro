# claude code changed: new file — shared OHLCV loading for the tool
# layer, so every tool reads local CSVs the same way rather than each
# tool file inventing its own timestamp-parsing convention.
#
# claude code changed: Multi-Asset Foundation Refactor, STEP 2. Path
# resolution now goes through bot/instruments.py's resolve_ohlcv_path()
# instead of calling fetch_all_symbols.symbol_to_filename() directly —
# this was one of two independent copies of the same provider-file
# convention (data_availability.py's _ohlcv_path() was the other; see
# that module). For CRYPTO this resolves to the exact same path as
# before — no existing data/*.csv file was renamed or moved.

import pandas as pd

from bot.instruments import UnknownInstrumentError, get_instrument, resolve_ohlcv_path


def load_ohlcv(asset: str) -> pd.DataFrame:
    """Reads the local, already-fetched OHLCV CSV for one symbol — never a
    live network call. Raises FileNotFoundError if the data isn't there;
    callers reach this only after the Data Availability Checker already
    confirmed it exists, so this is a defensive re-check, not the primary
    guard."""
    try:
        path = resolve_ohlcv_path(asset)
    except UnknownInstrumentError as exc:
        # claude code changed: new — Multi-Asset Foundation Refactor STEP
        # 2. Preserves this function's own documented contract ("Raises
        # FileNotFoundError if the data isn't there") for a symbol with no
        # registered instrument identity at all — the SAME externally
        # observable failure (fails closed with a clear "no OHLCV file"
        # message) callers/tests already depend on, even though internally
        # it's now routed through the richer instrument boundary. The
        # more precise UnknownInstrumentError is still what
        # data_availability.py's _ohlcv_check() catches directly for its
        # own richer REQUIRES_ASSET_CLASS/UNAVAILABLE status distinction —
        # this translation only happens at this one, narrower call site.
        raise FileNotFoundError(f"no OHLCV file for '{asset}' — {exc}") from exc

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
    # claude code changed: new — attach instrument identity as DataFrame
    # metadata (pandas .attrs), not a new column. Purely additive: shape,
    # columns, and dtypes are byte-for-byte identical to before, so every
    # existing consumer (FeatureCalculator, the backtester, cointegration's
    # price Series extraction) is unaffected. This is the "normalized
    # research dataset" boundary section 6 of the refactor brief asked
    # for, sized to what one real provider actually needs today rather
    # than a speculative schema no second provider exists yet to test.
    df.attrs["instrument"] = get_instrument(asset)
    return df
