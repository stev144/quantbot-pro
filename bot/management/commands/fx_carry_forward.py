# bot/management/commands/fx_carry_forward.py  # claude code changed: 2026-10-28 — new: FX carry study step 3 (FXC-F1)
"""Frozen forward paper position FXC-F1 (FX_CARRY_FORWARD_PREREGISTRATION_2026-10-28.md). Paper only, no orders.

    python manage.py fx_carry_forward --register "Name"   start the clock (once; refuses a second time)
    python manage.py fx_carry_forward --update            daily: snapshot the broker's live swap/spread (MT5 open,
                                                          read-only), replay the book from T0, write the report
    python manage.py fx_carry_forward --status            replay and print the report without contacting MT5

Schedule --update once a day after the 17:00 New York rollover (e.g. 23:30 UTC) with Windows Task Scheduler, after
`refresh_forex_data --attach` has appended the day's candles."""
import json
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from bot.research import fx_carry_forward as F

REPORT = Path("research_data/fx_carry/forward/report_FXC-F1.json")


class Command(BaseCommand):
    help = "FXC-F1 frozen forward paper carry position (register / update / status)"

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument("--register", metavar="NAME")
        g.add_argument("--update", action="store_true")
        g.add_argument("--status", action="store_true")

    def handle(self, *args, **o):
        try:
            if o["register"]:
                led = F.register(o["register"])
                self.stdout.write(f"registered {F.REGISTRATION_ID} at {led['events'][-1]['at']} "
                                  f"(design sha256 {led['events'][-1]['design_sha256'][:12]}…)")
                o["update"] = True                                # take the first broker snapshot now
            if o["update"]:
                from bot.research.fx_carry_accounting import fetch_broker_terms, terms_to_dict
                terms = terms_to_dict(fetch_broker_terms(list(F.PAIRS)))
                missing = [p for p in F.PAIRS if p not in terms]
                if missing:
                    raise CommandError(f"broker did not return {missing}")
                F.add_snapshot(terms)
            state = F.replay(F.read_ledger())
            rep = F.report(state)
        except F.ForwardTestError as e:
            raise CommandError(str(e))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"generated_utc": pd.Timestamp.now(tz="UTC").isoformat(), **rep}, indent=1,
                                     default=str), encoding="utf-8")
        self.stdout.write(json.dumps(rep, indent=1, default=str))
