# claude code changed: new file — 2026-10-22, Futures Funding Intelligence Phase 1. Controlled, explicit research
# run of bot/research/funding_carry_monitor.py: reads recorded data before the sealed crypto holdout, writes
# research_data/funding_carry/funding_carry_report.json (+ monthly CSV, + provenance sidecars). The page only
# reads that file; nothing recomputes on a page view and nothing is scheduled.
from django.core.management.base import BaseCommand, CommandError

from bot.research import funding_carry_monitor as fcm


class Command(BaseCommand):
    help = "Descriptive funding-carry monitor (long spot + short perp) on recorded Binance data before the seal."

    def add_arguments(self, parser):
        parser.add_argument("--symbols", nargs="*", help="e.g. BTC/USDT ETH/USDT (default: every recorded funding file)")
        parser.add_argument("--notional", type=float, default=fcm.CarryConfig.spot_notional_usd)
        parser.add_argument("--data-dir", default="data")
        parser.add_argument("--out-dir", default=str(fcm.DEFAULT_OUT_DIR))

    def handle(self, *args, **opts):
        try:
            cfg = fcm.CarryConfig(spot_notional_usd=opts["notional"])
        except ValueError as e:
            raise CommandError(str(e))
        report = fcm.run_monitor(opts["symbols"], cfg, data_dir=opts["data_dir"], out_dir=opts["out_dir"])
        self.stdout.write(f"run_id {report['run_id'][:16]}…  assets {report['n_assets']}  OK {report['n_ok']}  "
                          f"→ {opts['out_dir']}/{fcm.REPORT_NAME}")
        for a in report["assets"]:
            if a["status"] != fcm.OK:
                self.stdout.write(f"  {a['symbol']}: {a['status']} — {a['reason']}")
