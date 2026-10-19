# ============================================================
# bot/management/commands/record_journal_backtest.py
# claude code changed: new file — 2026-10-07. Runs ONE existing-backtester backtest with the Phase 1 decision
# recorder switched on, so the Decision Journal pages (/research/journal/) have a run to show. Uses the canonical
# price-file resolver (bot.instruments.resolve_ohlcv_path) and the unchanged backtest() wrapper; the recorder only
# observes (backtest results are identical with it on or off). Crypto spot only (Phase 1 scope); never contacts an
# exchange — it reads the local CSV.
# ============================================================

import pandas as pd
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run one backtest with the decision-journal recorder on (Phase 1, crypto, local data only)."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="BTC_USDT", help="e.g. BTC_USDT (local data/<symbol>_1h.csv)")
        parser.add_argument("--candles", type=int, default=2000, help="most recent N candles (default 2000)")
        parser.add_argument("--venue", default="binance", help="cost model venue (default binance)")
        parser.add_argument("--end", default=None, help="last candle open time (UTC, e.g. 2023-06-01); default = latest")   # claude code changed: 2026-10-07 Phase 2 — --end (record a run inside a chosen zone, e.g. zone S)
        parser.add_argument("--allow-unvalidated", action="store_true",
                            help="let the router route to strategies without a production-eligible verdict "
                                 "(default: off, as in production — decisions are then SYSTEM_REJECTED / STRATEGY_NOT_VALIDATED)")

    def handle(self, *args, **opts):
        from bot.backtesting.backtester import backtest
        from bot.instruments import resolve_ohlcv_path
        from bot.journal.decision_recorder import BacktestDecisionRecorder
        from bot.journal.models import JournalRun
        canonical = opts["symbol"].replace("_", "/", 1)
        path = resolve_ohlcv_path(canonical)
        if not path.exists():
            raise CommandError(f"no local price file for {canonical}: {path}")
        if opts["candles"] < 1:
            raise CommandError("--candles must be >= 1")
        df = pd.read_csv(path, float_precision="round_trip")   # claude code changed: 2026-10-07 Phase 2 — exact parse (same as the labeler's provenance check)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        if opts["end"]:   # claude code changed: 2026-10-07 Phase 2 — --end (record a run inside a chosen zone, e.g. zone S)
            df = df[df.index <= pd.Timestamp(opts["end"], tz="UTC")]
        df = df.tail(opts["candles"])
        rec = BacktestDecisionRecorder(opts["symbol"])
        result = backtest(df, allow_unvalidated_strategies=opts["allow_unvalidated"], venue_id=opts["venue"],
                          journal_recorder=rec)
        if rec.run is None:
            raise CommandError("the recorder could not create a run (see the log); the backtest itself completed")
        run = JournalRun.objects.get(pk=rec.run.pk)
        self.stdout.write(f"run {run.run_id}: {run.status}{' ' + str(run.incomplete_reasons) if run.incomplete_reasons else ''} — "
                          f"{run.n_decisions} decisions over {run.coverage.get('n_candles')} candles, "
                          f"{len(result.get('trades') or [])} simulated trades")
        self.stdout.write(f"view it at /research/journal/{run.run_id}/")
