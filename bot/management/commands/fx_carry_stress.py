# bot/management/commands/fx_carry_stress.py  # claude code changed: 2026-10-28 — new: FX carry study step 2
"""python manage.py fx_carry_stress [--carry-snapshot PATH] [--refresh-rates]
Stress test of vol-sized LONG USD/JPY + LONG USD/CHF carry. Rates come from FRED (public), cached with provenance in
research_data/fx_carry/rates/. Writes research_data/fx_carry/stress_<UTC>.json. EXPLORATORY — sets risk limits only."""
import glob
import hashlib
import io
import json
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from bot.research import fx_carry_stress as S
from bot.research.fx_carry_accounting import load_hourly_close

RATES_DIR = Path("research_data/fx_carry/rates")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"


def load_rates(refresh: bool) -> dict:
    import requests
    RATES_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for sid in (S.USD_RATE_SERIES, *S.QUOTE_RATE_SERIES.values()):
        path = RATES_DIR / f"{sid}.csv"
        if refresh or not path.exists():
            r = requests.get(FRED_URL.format(sid), timeout=30)
            r.raise_for_status()
            path.write_text(r.text, encoding="utf-8")
            (RATES_DIR / f"{sid}.provenance.json").write_text(json.dumps({
                "source": FRED_URL.format(sid), "fetched_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "sha256": hashlib.sha256(r.text.encode()).hexdigest()}, indent=1), encoding="utf-8")
        df = pd.read_csv(path, na_values=["."])
        s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(),
                      index=pd.to_datetime(df.iloc[:, 0], utc=True)).dropna()
        out[sid] = s
    return out


class Command(BaseCommand):
    help = "Stress test of vol-sized LONG USD/JPY + LONG USD/CHF carry (risk limits, exploratory)"

    def add_arguments(self, parser):
        parser.add_argument("--carry-snapshot", default=None, help="carry accounting JSON (default: latest)")
        parser.add_argument("--refresh-rates", action="store_true")

    def handle(self, *args, **opts):
        snap_path = opts["carry_snapshot"] or (sorted(glob.glob("research_data/fx_carry/carry_*.json")) or [None])[-1]
        if not snap_path:
            raise CommandError("no carry snapshot — run `python manage.py fx_carry_accounting` first")
        snap = {r["symbol"]: r for r in json.loads(Path(snap_path).read_text())["table"]}
        rates = load_rates(opts["refresh_rates"])
        closes = {}
        for p in S.PAIRS:
            h = load_hourly_close(p)
            closes[p] = h.resample("1D").last().dropna()
        results = {}
        for tv in (0.05, 0.10, 0.15):
            cfg = S.StressConfig(target_vol=tv,
                                 markups_pct_yr={p: snap[p]["broker_markup_pct_yr"] for p in S.PAIRS},
                                 spreads_pct={p: snap[p]["round_trip_pct"] for p in S.PAIRS})
            res = S.run_stress(closes, rates, cfg)
            results[f"target_vol_{int(tv * 100)}pct"] = S.summarise(res)
        last = {sid: [str(s.index[-1].date()), float(s.iloc[-1])] for sid, s in rates.items()}
        payload = {"label": "EXPLORATORY — carry stress test to set risk limits; pseudo-holdout history, not edge evidence",
                   "carry_snapshot": snap_path, "rates_last_obs": last,
                   "rules": {"vol_lookback": S.VOL_LOOKBACK, "leverage_cap": S.LEVERAGE_CAP,
                             "cost_safety": S.COST_SAFETY, "episodes": S.EPISODES},
                   "results": results}
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        path = Path("research_data/fx_carry") / f"stress_{stamp}.json"
        with open(path, "x", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, default=str)
        self.stdout.write(json.dumps(payload, indent=1, default=str))
        self.stdout.write(f"\nwritten: {path}")
