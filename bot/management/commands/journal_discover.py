# ============================================================
# bot/management/commands/journal_discover.py
# claude code changed: new file — 2026-10-07 Decision Journal Phase 7. Runs the statsmodels-logistic hypothesis
# discovery (bot/journal/discovery.py) on one labelled backtest run, zone S only. Output: candidates for OWNER
# REVIEW (never registered automatically, never used by trading code).
# ============================================================
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Exploratory hypothesis discovery on one labelled backtest journal run (zone S; candidates only)."

    def add_arguments(self, parser):
        parser.add_argument("--run", required=True)

    def handle(self, *args, **opts):
        from bot.journal.discovery import DiscoveryRefused, discover
        from bot.journal.models import JournalRun
        try:
            out = discover(JournalRun.objects.get(run_id=opts["run"]))
        except JournalRun.DoesNotExist:
            raise CommandError(f"no run {opts['run']}")
        except DiscoveryRefused as e:
            raise CommandError(f"refused: {e}")
        self.stdout.write(f"{out['n_episodes']} episodes, {out['n_folds']} folds, out-of-fold log-loss "
                          f"{out['oof_logloss_model']:.4f} vs base rate {out['oof_logloss_base_rate']:.4f}; "
                          f"{len(out['candidates'])} candidate(s) for owner review -> {out['output_path']}")
        self.stdout.write(out["status"])
