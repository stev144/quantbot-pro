# bot/management/commands/fx_carry_accounting.py  # claude code changed: 2026-10-28 — new: FX carry accounting snapshot
"""python manage.py fx_carry_accounting — live carry vs cost vs risk for every forex pair the MT5 broker offers.
Needs the MT5 terminal running and logged in (read-only attach). Writes research_data/fx_carry/carry_<UTC>.json."""
import json
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand

from bot.research import fx_carry_accounting as fca


class Command(BaseCommand):
    help = "FX carry accounting: broker swap carry per side vs round-trip spread cost vs realised volatility"

    def add_arguments(self, parser):
        parser.add_argument("--out", default="research_data/fx_carry")

    def handle(self, *args, **opts):
        terms = fca.fetch_broker_terms(fca.forex_universe())
        table = fca.build_carry_table(terms)
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        out = Path(opts["out"]); out.mkdir(parents=True, exist_ok=True)
        path = out / f"carry_{stamp}.json"
        payload = {"label": "DESCRIPTIVE — live broker carry accounting, not a backtest or a signal",
                   "created_utc": stamp, "vol_window_days": fca.VOL_WINDOW_DAYS, "cost_multiple": fca.COST_MULTIPLE,
                   "terms": fca.terms_to_dict(terms),
                   "table": json.loads(table.reset_index().to_json(orient="records"))}
        with open(path, "x", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        cols = ["paid_side", "best_carry_pct_yr", "round_trip_pct", "vol_ann_pct", "best_carry_to_vol",
                "days_to_3x_cost_long", "days_to_3x_cost_short", "broker_markup_pct_yr", "problem"]
        with pd.option_context("display.width", 200, "display.max_rows", 200, "display.float_format", "{:.3f}".format):
            self.stdout.write(table[[c for c in cols if c in table]].to_string())
        self.stdout.write(f"\nwritten: {path}")
