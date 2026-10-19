# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/journal_replay.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 6 test helper. A deterministic, offline
# REPLAY source for the DRY_RUN live loop: it serves real local candles (data/*.csv) one hour at a time through the
# few ccxt methods the DRY_RUN path uses (fetch_ohlcv, fetch_ticker, parse_timeframe, milliseconds). It exists to
# compare the bot's OWN behaviour with the journal recorder ON vs OFF on identical inputs — it is not a model of
# exchange behaviour (exchange-facing tests keep using real endpoints, per project convention).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import pandas as pd


class ReplayExchange:
    id = "binance"

    def __init__(self, csv_path: str, *, start_row: int, limit_rows: int = 300):
        df = pd.read_csv(csv_path, float_precision="round_trip")
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        self.df = df.reset_index(drop=True)
        self.cursor = start_row            # index of the LAST CLOSED candle served
        self.limit_rows = limit_rows
        self.markets = {}

    # -- clock: "now" is just after the close of the cursor candle, so drop_forming_candles keeps it -----------
    def milliseconds(self):
        return int((self.df.loc[self.cursor, "timestamp"] + pd.Timedelta(hours=1, minutes=1)).value // 10**6)

    def parse_timeframe(self, tf):
        assert tf == "1h"
        return 3600

    def advance(self, n=1):
        self.cursor += n

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=300, since=None, params=None):
        lo = max(0, self.cursor - min(limit, self.limit_rows) + 1)
        w = self.df.iloc[lo:self.cursor + 1]
        return [[int(t.value // 10**6), o, h, l, c, v] for t, o, h, l, c, v in
                zip(w["timestamp"], w["open"], w["high"], w["low"], w["close"], w["volume"])]

    def fetch_ticker(self, symbol):
        return {"symbol": symbol, "last": float(self.df.loc[self.cursor, "close"])}

    # -- harmless metadata used by adapters/validators ----------------------------------------------------------
    def load_markets(self, *a, **k):
        return self.markets

    def amount_to_precision(self, symbol, amount):
        return str(amount)

    def price_to_precision(self, symbol, price):
        return str(price)

    def fetch_open_orders(self, *a, **k):
        return []

    def fetch_balance(self, *a, **k):
        return {"total": {}, "free": {}}
