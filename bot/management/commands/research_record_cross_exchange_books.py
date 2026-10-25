# claude code changed: new file — 2026-10-25, Phase A cross-exchange recorder (RESEARCH-ONLY). Started explicitly by
# the user, stopped with Ctrl+C (or --minutes). Public market data only: no API key, no order/cancel/withdrawal path,
# not connected to bot_runner.py, the execution engine or any scheduler.
import asyncio

from django.core.management.base import BaseCommand, CommandError

from bot.research.cross_exchange import RECORDER_VERSION
from bot.research.cross_exchange import feeds
from bot.research.cross_exchange.config import DEFAULT_INSTRUMENTS, RecorderConfig, instruments_from_bases
from bot.research.cross_exchange.recorder import Recorder, read_session, summary_text

BANNER = """
==============================================================================
 RESEARCH-ONLY market-data recorder (cross-exchange, Phase A)  {version}
 - public order-book data only; no API keys; no orders, cancels or withdrawals
 - not connected to the trading bot, execution engine or any scheduler
 - recording measures quotes; it does not show an opportunity or a profit
==============================================================================
 session   {session}
 venues    {venues}
 assets    {assets}
 sampling  every {interval:g} s, {depth} levels per side   (stale after {stale:g} s)
 duration  {duration}
 output    {out}
 stop      Ctrl+C  (files are flushed and the session summary is written)
==============================================================================
"""


class Command(BaseCommand):
    help = "RESEARCH-ONLY: record public order books from Binance, Kraken and Coinbase (cross-exchange Phase A)."

    def add_arguments(self, parser):
        parser.add_argument("--assets", nargs="*", help="base assets quoted in USDT on every venue (default BTC ETH SOL XRP)")
        parser.add_argument("--venues", nargs="*", default=list(RecorderConfig.venues))
        parser.add_argument("--interval", type=float, default=RecorderConfig.sample_interval_s, help="seconds between samples")
        parser.add_argument("--depth", type=int, default=RecorderConfig.depth, help="levels per side in each sample")
        parser.add_argument("--stale-after", type=float, default=RecorderConfig.stale_after_s)
        parser.add_argument("--minutes", type=float, default=None, help="stop after N minutes (default: until Ctrl+C)")
        parser.add_argument("--rotate-mb", type=float, default=RecorderConfig.rotate_mb)
        parser.add_argument("--out", default=RecorderConfig.out_dir)

    def handle(self, *args, **o):
        try:
            cfg = RecorderConfig(
                instruments=instruments_from_bases(o["assets"]) if o["assets"] else DEFAULT_INSTRUMENTS,
                venues=tuple(v.lower() for v in o["venues"]), sample_interval_s=o["interval"], depth=o["depth"],
                stale_after_s=o["stale_after"], max_duration_s=o["minutes"] * 60 if o["minutes"] else None,
                rotate_mb=o["rotate_mb"], out_dir=o["out"])
        except ValueError as e:
            raise CommandError(str(e))
        rec = Recorder(cfg, clock_fn=feeds.clock_offsets, listing_fn=feeds.check_listing)
        self.stdout.write(BANNER.format(version=RECORDER_VERSION, session=rec.session_id, venues=", ".join(cfg.venues),
                                        assets=", ".join(i.key for i in cfg.instruments), interval=cfg.sample_interval_s,
                                        depth=cfg.depth, stale=cfg.stale_after_s,
                                        duration=f"{o['minutes']:g} min" if o["minutes"] else "manual (Ctrl+C)",
                                        out=f"{cfg.out_dir}/raw/{rec.session_id}/"))
        try:
            asyncio.run(rec.run())
        except KeyboardInterrupt:
            self.stdout.write("\nStopped by user — files flushed.")
        meta, _obs, _ev = read_session(cfg.out_dir, rec.session_id)
        self.stdout.write(summary_text(meta))
