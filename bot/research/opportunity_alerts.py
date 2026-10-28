# bot/research/opportunity_alerts.py  # claude code changed: 2026-10-28 — new: plain-English alerts on state CHANGES
"""Checks the research monitors and returns the CURRENT state of each thing worth knowing about; the runner compares
with the last saved state and notifies only on a change (no repeated alerts every hour).

Watched (reusing existing code, nothing re-computed differently):
  funding:<coin>        funding_watch: ON (clears the cost hurdle) / OFF / UNKNOWN
  funding:several_on    3 or more watched coins ON at once — the study's moment to write and freeze a forward test
  fxc:drawdown          FXC-F1 forward paper position: OK / NEAR_LIMIT (≤ −7 %, the stop is −10 %) / STOPPED
  fxc:updates           FXC-F1 broker snapshot freshness: OK / STALE (no snapshot for 3+ days → the daily job is not running)
  fxc:carry:<pair>      the latest broker swap for the held pair: PAYS / NO_LONGER_PAYS
Descriptive alerts, never trade instructions."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)
STATE_FILE = Path("research_data/alerts/state.json")
SEVERAL_ON = 3
FXC_NEAR_LIMIT_PCT = -7.0
SNAPSHOT_STALE_DAYS = 3
SITE = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Condition:
    key: str
    state: str
    title: str = ""
    message: str = ""
    priority: str = "default"
    tags: str = ""
    url: str = ""
    notify: bool = True          # False: record the state silently (e.g. a return to normal not worth a ping)


def funding_conditions(watch: Dict) -> List[Condition]:
    if not watch.get("coins"):
        return []
    out, n_on = [], 0
    for c in watch["coins"]:
        sym, st = c["symbol"], c["status"]
        if st == "ON":
            n_on += 1
            out.append(Condition(
                f"funding:{sym}", "ON", f"{sym} funding cleared the cost hurdle",
                f"The last 7 days of {sym} funding paid {c['f7_pct']:.2f}% ({c['times_hurdle']:.1f}× the 0.50% round-trip "
                f"cost, ≈{c['annual_equiv_pct']:.0f}% a year if it lasted). The funding-carry rule would be IN. "
                "Not a trade signal — watch whether more coins join.", "high", "moneybag",
                f"{SITE}/research/funding-carry/"))
        elif st in ("BELOW_HURDLE", "NEGATIVE"):
            out.append(Condition(f"funding:{sym}", "OFF", f"{sym} funding back below the hurdle",
                                 f"{sym}: the 7-day funding is {c['f7_pct']:.2f}%, below the 0.50% hurdle — the rule would be OUT.",
                                 "low", "chart_with_downwards_trend", f"{SITE}/research/funding-carry/"))
    if watch.get("available"):
        if n_on >= SEVERAL_ON:
            out.append(Condition("funding:several_on", "YES", f"{n_on} coins clear the funding hurdle",
                                 f"{n_on} of {len(watch['coins'])} watched coins pay enough funding to cover costs — the "
                                 "conditions of 2024. This is the moment the study set for writing and freezing a forward "
                                 "test, before trading anything. Ask Claude to draft it.", "urgent", "rotating_light",
                                 f"{SITE}/research/funding-carry/"))
        else:
            out.append(Condition("funding:several_on", "NO", notify=False))
    return out


def fxc_conditions(state: Optional[Dict], report: Optional[Dict], now: pd.Timestamp) -> List[Condition]:
    if state is None:
        return []
    out = []
    url = f"{SITE}/research/funding-carry/"
    if report and report.get("days"):
        if report.get("stopped"):
            out.append(Condition("fxc:drawdown", "STOPPED", "FX carry test stopped at its −10% loss limit",
                                 f"FXC-F1 hit its loss limit on {report['stopped']} and closed. Equity ${report['equity']:,.2f}. "
                                 "By its rules it does not restart. Ask Claude for the stop report.", "urgent", "warning"))
        elif report["current_drawdown_pct"] <= FXC_NEAR_LIMIT_PCT:
            out.append(Condition("fxc:drawdown", "NEAR_LIMIT", "FX carry test close to its loss limit",
                                 f"FXC-F1 is {report['current_drawdown_pct']:.1f}% below its peak; it stops at −10%. Nothing to do — "
                                 "the rule acts by itself.", "high", "warning"))
        else:
            out.append(Condition("fxc:drawdown", "OK", "FX carry test back within its normal range",
                                 f"FXC-F1 is {report['current_drawdown_pct']:.1f}% below its peak.", "low", notify=True))
    snaps = [e for e in state.get("_ledger_events", []) if e["type"] == "SNAPSHOT"]
    if snaps:
        last = pd.Timestamp(snaps[-1]["at"])
        stale = now - last > pd.Timedelta(days=SNAPSHOT_STALE_DAYS)
        out.append(Condition("fxc:updates", "STALE" if stale else "OK",
                             "FX carry test: daily update not running" if stale else "FX carry test updates resumed",
                             (f"No broker snapshot since {last:%d %b %H:%M} UTC. Check that MT5 is open and the daily task "
                              "'QuantBot - FX Carry Forward Update' exists.") if stale else "Daily updates are arriving again.",
                             "high" if stale else "low", "electric_plug"))
        for pair, t in snaps[-1]["terms"].items():
            pays = float(t["swap_long"]) > 0
            out.append(Condition(f"fxc:carry:{pair}", "PAYS" if pays else "NO_LONGER_PAYS",
                                 f"{pair} carry {'pays again' if pays else 'no longer pays'}",
                                 (f"The broker's swap for holding {pair} long is {t['swap_long']} points a day — "
                                  + ("it pays again." if pays else "the carry the FX test relies on has gone. Ask Claude what it means for FXC-F1.")),
                                 "low" if pays else "high", "yen"))
    return out


def gather(now: Optional[pd.Timestamp] = None, watch: Optional[Dict] = None) -> List[Condition]:
    """Collect every condition from the live monitors. Each source fails on its own (logged), never the whole run."""
    now = now or pd.Timestamp.now(tz="UTC")
    conds: List[Condition] = []
    try:
        if watch is None:
            from bot.research.funding_watch import compute_watch
            watch = compute_watch(now=now)
        conds += funding_conditions(watch)
    except Exception as e:
        logger.warning("alerts: funding watch failed: %s", e)
    try:
        from bot.research import fx_carry_forward as F
        ledger = F.read_ledger()
        if any(e["type"] == "REGISTERED" for e in ledger["events"]):
            rep = F.report(F.replay(ledger))
            conds += fxc_conditions({"_ledger_events": ledger["events"]}, rep, now)
    except Exception as e:
        logger.warning("alerts: FXC-F1 check failed: %s", e)
    return conds


ACTIONABLE_ON_FIRST_SIGHT = {"ON", "YES", "STOPPED", "NEAR_LIMIT", "STALE", "NO_LONGER_PAYS"}


def run(conditions: List[Condition], send: Callable[..., bool], state_path: Path = STATE_FILE,
        now: Optional[pd.Timestamp] = None, dry_run: bool = False) -> List[Dict]:
    """Notify on state changes only. First sight of a key: notify only if its state is actionable (an opportunity or a
    problem), otherwise record it silently. Returns the notifications made (or that would be, in dry-run)."""
    now = now or pd.Timestamp.now(tz="UTC")
    saved = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"states": {}, "log": []}
    sent = []
    for c in conditions:
        prev = saved["states"].get(c.key)
        if prev == c.state:
            continue
        first = prev is None
        should = c.notify and (c.state in ACTIONABLE_ON_FIRST_SIGHT if first else True)
        ok = None
        if should:
            ok = True if dry_run else send(c.title, c.message, priority=c.priority, tags=c.tags, click=c.url or None)
            sent.append({"key": c.key, "from": prev, "to": c.state, "title": c.title, "sent": ok})
        if dry_run:
            continue
        if not should or ok:                     # a failed push is retried next run (state not advanced)
            saved["states"][c.key] = c.state
            saved["log"].append({"at": now.isoformat(), "key": c.key, "from": prev, "to": c.state, "notified": bool(should)})
    if not dry_run:
        saved["log"] = saved["log"][-500:]
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return sent
