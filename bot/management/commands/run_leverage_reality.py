# claude code changed: new file — 2026-10-22, Leverage Reality Phase 2. Controlled, explicit run of the DESCRIPTIVE
# historical summaries (ladder wipeouts, 30-day leveraged holds) on recorded data before the sealed crypto holdout.
# Writes research_data/leverage_reality/historical_summary.json (+ provenance); the Academy page only reads it.
from django.core.management.base import BaseCommand

from bot.research import leverage_reality as lr


class Command(BaseCommand):
    help = "Descriptive historical leverage summaries (ladder wipeouts, 30-day holds) for the Leverage Reality page."

    def add_arguments(self, parser):
        parser.add_argument("--data-dir", default="data")
        parser.add_argument("--out-dir", default=str(lr.DEFAULT_OUT_DIR))

    def handle(self, *args, **opts):
        res = lr.run_historical(data_dir=opts["data_dir"], out_dir=opts["out_dir"])
        self.stdout.write(f"run_id {res['run_id'][:16]}…  files {res['data']['n_files']}  → {opts['out_dir']}/{lr.SUMMARY_NAME}")
        for r in res["ladder"]:
            self.stdout.write(f"  ladder {r['leverage']:g}x {r['spacing_pct']:g}% {r['bound']}: cycles {r['cycles']} "
                              f"wipeout share {r['wipeout_share_of_resolved']}")
        for r in res["hold"]:
            self.stdout.write(f"  hold {r['leverage']}x: windows {r['windows']} liquidated {r['liquidated_share']}")
