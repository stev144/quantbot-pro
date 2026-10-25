# claude code changed: new file — 2026-10-25, cross-exchange research PHASE B (descriptive gap study). Reads a recorded
# session (default: the newest), writes a NEW report file under <out>/reports/ (never overwrites) and prints a summary.
# Research-only: no exchange connection, no orders, no signals. See CROSS_EXCHANGE_PHASE_B_GAP_STUDY_2026-10-25.md.
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.research.cross_exchange.config import RecorderConfig
from bot.research.cross_exchange.gap_study import CLASSIFICATION, StudyConfig, default_fees, run_study, save_report


class Command(BaseCommand):
    help = "RESEARCH-ONLY Phase B: descriptive cross-exchange gap study over a recorded order-book session."

    def add_arguments(self, parser):
        parser.add_argument("--session", default=None, help="session id (default: newest under --out)")
        parser.add_argument("--sizes", default="1000,10000,50000", help="trade sizes in the quote currency (USDT)")
        parser.add_argument("--max-feed-delay-ms", type=float, default=1000.0)
        parser.add_argument("--fee", action="append", default=[], metavar="VENUE=RATE",
                            help="override a taker fee fraction, e.g. --fee binance=0.00075 (your account tier)")
        parser.add_argument("--out", default=None, help="recorder output folder (default: the recorder's)")

    def handle(self, *args, **o):
        out = o["out"] or str(Path(settings.BASE_DIR) / RecorderConfig().out_dir)
        try:
            sizes = tuple(int(x) for x in o["sizes"].split(",") if x.strip())
        except ValueError:
            raise CommandError("--sizes must be comma-separated whole numbers")
        if not sizes or any(s <= 0 for s in sizes):
            raise CommandError("--sizes must be positive")
        fees, basis = default_fees()
        for item in o["fee"]:
            venue, _, rate = item.partition("=")
            try:
                r = float(rate)
            except ValueError:
                raise CommandError(f"bad --fee {item!r}")
            if not (0 <= r < 0.05):
                raise CommandError(f"--fee {item!r}: rate must be a fraction between 0 and 0.05")
            fees[venue.strip().lower()] = r
            basis[venue.strip().lower()] = "OWNER OVERRIDE on the command line"
        self.stdout.write(self.style.WARNING("RESEARCH-ONLY · " + CLASSIFICATION))
        cfg = StudyConfig(sizes_quote=sizes, max_feed_delay_ms=o["max_feed_delay_ms"], fees=fees, fee_basis=basis)
        try:
            report = run_study(out, o["session"], cfg)
        except (FileNotFoundError, ValueError) as e:
            raise CommandError(str(e))
        path = save_report(report, out)
        i = report["input"]
        self.stdout.write(f"session {report['session']['session_id']} · {i['instants']} instants "
                          f"{i['first_sample_utc'][:19]} → {i['last_sample_utc'][:19]} UTC · {report['runtime_s']} s")
        for inst in report["instruments"]:
            for p in inst["pairs"]:
                top = p["by_size"]["top"]
                if not top or not top["gross_bps"]:
                    continue
                g, n = top["gross_bps"], top["net_bps"]
                self.stdout.write(f"  {inst['instrument']:9s} buy {p['buy_venue']:8s} sell {p['sell_venue']:8s} "
                                  f"top gross>0 {g['pct_positive']:5.1f}%  median {g['p50']:7.2f} bps  "
                                  f"net>0 {n['pct_positive']:5.1f}%  (fees {p['fee_bps_total']:.0f} bps)")
        self.stdout.write(self.style.SUCCESS(f"report written: {path}"))
