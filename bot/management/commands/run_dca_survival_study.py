# bot/management/commands/run_dca_survival_study.py  # claude code changed: 2026-10-21 — new
"""
python manage.py run_dca_survival_study            # full study -> research_data/dca_survival/<cutoff>/report.json
python manage.py run_dca_survival_study --refresh-costs   # first re-download MT5 spread history + broker snapshot

Historical simulation only (IS_PAPER_ONLY). Data are frozen at FX_RESEARCH_DATA_CUTOFF.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the FX DCA capital-survival study (fixed-capital replay, costs, stress, events) and save the report."

    def add_arguments(self, parser):
        parser.add_argument("--refresh-costs", action="store_true",
                            help="re-download MT5 spread history and the broker cost snapshot first (needs the MT5 terminal)")

    def handle(self, *args, **opts):
        if opts["refresh_costs"]:
            from bot.mt5_data_fetcher import download_mt5_spread_history
            from bot.research.fx_survival_study import PORTFOLIO_PAIRS
            self.stdout.write(str(download_mt5_spread_history(list(PORTFOLIO_PAIRS))))
        from bot.research.fx_survival_study import run_study
        path = run_study()
        self.stdout.write(self.style.SUCCESS(f"report: {path}"))
