# ============================================================
# bot/management/commands/label_journal_run.py
# claude code changed: new file — 2026-10-07 Decision Journal Phase 2. Writes the forward-return and counterfactual
# rule labels (bot/journal/labeler.py) for one recorded run. Insert-only, provenance-checked, local data only.
# ============================================================
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Label one decision-journal run (Phase 2 outcomes; local data only)."

    def add_arguments(self, parser):
        parser.add_argument("--run", required=True, help="JournalRun.run_id")

    def handle(self, *args, **opts):
        from bot.journal.labeler import LabelerRefused, label_run
        from bot.journal.models import JournalRun
        try:
            run = JournalRun.objects.get(run_id=opts["run"])
        except JournalRun.DoesNotExist:
            raise CommandError(f"no run {opts['run']}")
        try:
            out = label_run(run)
        except LabelerRefused as e:
            raise CommandError(f"refused: {e}")
        self.stdout.write(f"run {out['run_id']}: {out['labels']} labels ({out['label_version']}) by status {out['by_status']}")
