# ============================================================
# bot/management/commands/backfill_journal_legacy.py
# claude code changed: new file — 2026-10-07 Decision Journal Phase 3. Brings existing TradeRecord / ExecutionIncident
# rows into the journal as LEGACY decisions (bot/journal/legacy_backfill.py). DRY RUN unless --commit.
# ============================================================
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backfill existing journal rows as LEGACY decisions (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="actually write (default: dry run)")

    def handle(self, *args, **opts):
        from bot.journal.legacy_backfill import backfill
        out = backfill(commit=opts["commit"])
        self.stdout.write(f"{'COMMITTED' if out['committed'] else 'DRY RUN'}: {out['decisions']} legacy decision(s) "
                          f"in runs {out['runs']}; linked {out['trade_records_linked']} trade record(s), "
                          f"{out['incidents_linked']} incident(s)")
        for s in out["skipped"]:
            self.stdout.write(f"  skipped {s['source']} {s['pk']}: {s['reason']}")
