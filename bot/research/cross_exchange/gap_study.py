# claude code changed: new file — 2026-10-25, cross-exchange research PHASE B: descriptive gap study over recorded books.
#
# What it measures, per instrument, per ordered venue pair (buy on A, sell on B) and per trade size:
#   • top-of-book gross gap      (best bid on B − best ask on A) / best ask on A
#   • size-dependent gross gap   average SELL fill on B's bids vs average BUY fill on A's asks for the same coin quantity,
#                                walking the recorded depth (no extrapolation past the recorded levels)
#   • estimated net gap          the same after each venue's taker fee on its own leg
#   • how often each is > 0, its distribution, and how long positive runs ("episodes") last
# Every number is a DESCRIPTIVE measurement of recorded public books. It is NOT realised profit and NOT an executable
# opportunity: pre-funded inventory on both venues, simultaneous fills at the sampled instant, base-tier public fees
# and zero latency are assumed, and rebalancing between venues, network fees, partial fills, queue position and price
# movement during the feed delay are not modelled. Governance: owner-authorised 2026-10-25 as descriptive only inside
# the sealed crypto window ZONES.F — no inferential claim, no use for tuning anything evaluated on ZONES.F.
#
# The order-book walk is done here in exact Decimal on the recorded price text. bot/engines/execution_comparison.py has
# a float walker for live adapter books, but this package is deliberately isolated from bot.engines (SafetyTests) and
# Phase B must not round the recorded prices, so it is not reused.
import dataclasses
import hashlib
import json
import time
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from bot.research.cross_exchange.recorder import _effective_status, latest_session_id

STUDY_VERSION = "SQT-XARB-GAP-1"
REPORT_KIND = "XARB_GAP_STUDY"
CLASSIFICATION = ("DESCRIPTIVE MEASUREMENT of recorded public order books — not realised profit, not an executable "
                  "opportunity, no evidence status, no permission to trade")
GOVERNANCE = ("Owner-authorised 2026-10-25 (\"continue with phase b\") as DESCRIPTIVE analysis of recorder data inside "
              "the sealed crypto window ZONES.F (D-8). Conditions (strictest reading): no p-values or significance "
              "claims; nothing here may select or tune any strategy evaluated on ZONES.F; any later inferential test "
              "(Phase C) must be preregistered and use only data recorded after its registration.")
LIMITATIONS = (
    "Pre-funded inventory on both venues is assumed; moving coins or cash between venues (time, network fees, "
    "rebalancing) is not modelled.",
    "Both legs are assumed to fill at the sampled instant. Real orders arrive after the feed delay (≈ 75–210 ms "
    "measured) plus order latency; the book can move in between.",
    "Sampling is once per second, so episodes shorter than a second are invisible and episode lengths are only "
    "known to the nearest sample.",
    "Depth is limited to the recorded top levels per side; a size the recorded depth cannot fill is counted as "
    "'insufficient depth', never extrapolated.",
    "Fees are base-tier public taker rates (Binance and Coinbase unverified); account tiers, discounts or maker "
    "execution would change the net figures.",
    "Samples are strongly autocorrelated: '% of time' is a duration share, not a count of independent events.",
    "All venues are quoted in USDT here; USDT is not assumed equal to USD.",
)
BPS = Decimal(10000)


@dataclass(frozen=True)
class StudyConfig:
    sizes_quote: Tuple[int, ...] = (1000, 10000, 50000)    # trade size in the quote currency (USDT) on the buy leg
    max_feed_delay_ms: float = 1000.0    # a row whose last update reached this PC later than this is excluded
    max_gap_s: float = 2.5               # instants further apart than this break an episode (recorder pause / sleep)
    fees: Dict[str, float] = field(default_factory=dict)        # venue → taker fee fraction
    fee_basis: Dict[str, str] = field(default_factory=dict)     # venue → where the rate comes from

    def as_dict(self) -> dict:
        return {"sizes_quote": list(self.sizes_quote), "max_feed_delay_ms": self.max_feed_delay_ms,
                "max_gap_s": self.max_gap_s, "fees": dict(self.fees), "fee_basis": dict(self.fee_basis)}


def default_fees() -> Tuple[Dict[str, float], Dict[str, str]]:
    """Base-tier taker rates from the project's single cost table, with their provenance."""
    from bot.config.execution_costs import VENUE_EXECUTION_COSTS, VENUE_FEE_PROVENANCE
    fees = {v: float(c["fee_rate"]) for v, c in VENUE_EXECUTION_COSTS.items()}
    basis = {v: ("VERIFIED " if p.get("verified") else "UNVERIFIED ") + p.get("basis", "")
             for v, p in VENUE_FEE_PROVENANCE.items()}
    return fees, basis


# ── pure measurement (known-answer tested) ────────────────────────────────────────────────────────────────────────
def walk(levels, qty: Decimal) -> Optional[Decimal]:
    """Average fill price for `qty` coins walking [[price, size], …] best-first (text or Decimal); None if the levels
    cannot fill it."""
    remaining, cost = qty, Decimal(0)
    for p, q in levels:
        price, size = Decimal(p), Decimal(q)
        take = size if size < remaining else remaining
        cost += take * price
        remaining -= take
        if remaining <= 0:
            return cost / qty
    return None


def pair_measure(buy_row: dict, sell_row: dict, sizes_quote, fee_buy: float, fee_sell: float) -> dict:
    """Buy on buy_row's venue (lift its asks), sell on sell_row's venue (hit its bids). Gaps in basis points of the
    buy-side spend. Size-dependent legs use the SAME coin quantity = size / best ask on the buy venue."""
    ask, bid = Decimal(buy_row["best_ask"]), Decimal(sell_row["best_bid"])
    fb, fs = Decimal(str(fee_buy)), Decimal(str(fee_sell))
    out = {"top_gross_bps": float((bid - ask) / ask * BPS),
           "top_net_bps": float((bid * (1 - fs) - ask * (1 + fb)) / (ask * (1 + fb)) * BPS), "sizes": {}}
    for size in sizes_quote:
        qty = Decimal(size) / ask
        buy_avg, sell_avg = walk(_dec(buy_row, "asks"), qty), walk(_dec(sell_row, "bids"), qty)
        if buy_avg is None or sell_avg is None:
            out["sizes"][size] = None                                    # insufficient recorded depth
            continue
        spend = buy_avg * (1 + fb)
        out["sizes"][size] = {"gross_bps": float((sell_avg - buy_avg) / buy_avg * BPS),
                              "net_bps": float((sell_avg * (1 - fs) - spend) / spend * BPS)}
    return out


def _dec(row: dict, side: str):
    """The row's levels as Decimal pairs, converted once per row (a row is compared with every other venue)."""
    k = "_dec_" + side
    if k not in row:
        row[k] = [(Decimal(p), Decimal(q)) for p, q in row[side]]
    return row[k]


def row_exclusion(row: Optional[dict], max_feed_delay_ms: float) -> Optional[str]:
    """Why a row cannot be used (None = usable). Unusable books carry no prices, so nothing is ever filled in."""
    if row is None:
        return "missing_row"
    if not row.get("usable"):
        flags = row.get("flags") or []
        return "stale" if "STALE" in flags else f"unusable:{row.get('status')}"
    d = row.get("feed_delay_ms")
    if d is not None and d > max_feed_delay_ms:
        return "slow_feed"
    return None


# ── reading the session (streaming, fixed snapshot) ───────────────────────────────────────────────────────────────
def iter_instants(raw_dir: Path, digest) -> Iterator[Tuple[int, int, Dict[str, dict]]]:
    """Yields (sample_mono_ns, sample_utc_ns, {"venue:symbol": row}) per sampling instant, reading each observation file
    only up to the size it had when the study started (the recorder may still be writing). Torn lines are skipped."""
    files = sorted(raw_dir.glob("obs-*.jsonl"))
    limits = {p: p.stat().st_size for p in files}
    cur_key, cur_utc, cur = None, None, {}
    for p in files:
        with open(p, "rb") as fh:
            data = fh.read(limits[p])
        digest.update(data)
        for raw in data.split(b"\n"):
            if not raw:
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            k = r.get("sample_mono_ns")
            if k != cur_key:
                if cur:
                    yield cur_key, cur_utc, cur
                cur_key, cur_utc, cur = k, r.get("sample_utc_ns"), {}
            cur[f"{r.get('venue')}:{r.get('venue_symbol')}"] = r
    if cur:
        yield cur_key, cur_utc, cur


# ── aggregation ───────────────────────────────────────────────────────────────────────────────────────────────────
class _Cell:
    """One (instrument, buy venue, sell venue, size|'top') series: values, positive share, episodes."""
    __slots__ = ("gross", "net", "n", "insufficient", "runs_gross", "runs_net", "_rg", "_rn")

    def __init__(self):
        self.gross, self.net = array("d"), array("d")
        self.n = self.insufficient = 0
        self.runs_gross, self.runs_net = [], []
        self._rg = self._rn = 0

    def add(self, gross: Optional[float], net: Optional[float]):
        self.n += 1
        if gross is None:
            self.insufficient += 1
            self.close()
            return
        self.gross.append(gross)
        if net is not None:
            self.net.append(net)
        self._rg = self._rg + 1 if gross > 0 else self._end(self.runs_gross, self._rg)
        if net is not None:
            self._rn = self._rn + 1 if net > 0 else self._end(self.runs_net, self._rn)

    @staticmethod
    def _end(runs, length):
        if length:
            runs.append(length)
        return 0

    def close(self):
        self._rg = self._end(self.runs_gross, self._rg)
        self._rn = self._end(self.runs_net, self._rn)


def _dist(vals: array) -> Optional[dict]:
    if not len(vals):
        return None
    a = np.frombuffer(vals, dtype=np.float64)
    q = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
    return {"n": int(a.size), "mean": float(a.mean()), "min": float(a.min()), "max": float(a.max()),
            **{f"p{p}": float(v) for p, v in zip((1, 5, 25, 50, 75, 95, 99), q)},
            "pct_positive": float((a > 0).mean() * 100)}


def _episodes(runs: List[int], interval_s: float) -> dict:
    if not runs:
        return {"count": 0, "median_s": None, "p90_s": None, "max_s": None, "total_s": 0.0}
    r = np.asarray(runs, dtype=np.float64) * interval_s
    return {"count": len(runs), "median_s": float(np.median(r)), "p90_s": float(np.percentile(r, 90)),
            "max_s": float(r.max()), "total_s": float(r.sum())}


def run_study(out_dir: str, session_id: Optional[str] = None, cfg: Optional[StudyConfig] = None) -> dict:
    t0 = time.time()
    cfg = cfg or StudyConfig()
    if not cfg.fees:
        fees, basis = default_fees()
        cfg = dataclasses.replace(cfg, fees=fees, fee_basis=basis)
    root = Path(out_dir)
    session_id = session_id or latest_session_id(out_dir)
    if not session_id:
        raise FileNotFoundError(f"no recording session under {out_dir}")
    meta = _effective_status(json.loads((root / "metadata" / f"{session_id}.json").read_text(encoding="utf-8")))
    interval = float((meta.get("config") or {}).get("sample_interval_s") or 1.0)
    missing = [v for v in (meta.get("config") or {}).get("venues", []) if v not in cfg.fees]
    if missing:
        raise ValueError(f"no fee configured for venue(s) {missing}")

    # which books belong to which instrument
    books_by_inst: Dict[str, List[str]] = defaultdict(list)
    for key, b in (meta.get("books") or {}).items():
        books_by_inst[b.get("instrument")].append(key)

    cells: Dict[tuple, _Cell] = defaultdict(_Cell)
    excluded: Dict[str, Counter] = defaultdict(Counter)
    usable_rows: Counter = Counter()
    digest = hashlib.sha256()
    n_instants, first_utc, last_utc, prev_mono, breaks = 0, None, None, None, 0
    for mono, utc, rows in iter_instants(root / "raw" / session_id, digest):
        n_instants += 1
        first_utc = utc if first_utc is None else first_utc
        last_utc = utc
        if prev_mono is not None and mono is not None and (mono - prev_mono) / 1e9 > cfg.max_gap_s:
            breaks += 1
            for c in cells.values():
                c.close()                                               # a recorder pause ends every episode
        prev_mono = mono
        for inst, keys in books_by_inst.items():
            ok = {}
            for k in keys:
                why = row_exclusion(rows.get(k), cfg.max_feed_delay_ms)
                if why:
                    excluded[k][why] += 1
                else:
                    ok[k] = rows[k]
                    usable_rows[k] += 1
            for a in keys:
                for b in keys:
                    if a == b:
                        continue
                    va, vb = a.split(":", 1)[0], b.split(":", 1)[0]
                    base = (inst, va, vb)
                    if a not in ok or b not in ok:
                        for s in ("top", *cfg.sizes_quote):
                            if (*base, s) in cells:
                                cells[(*base, s)].close()               # an ineligible instant ends the episode
                        continue
                    m = pair_measure(ok[a], ok[b], cfg.sizes_quote, cfg.fees[va], cfg.fees[vb])
                    cells[(*base, "top")].add(m["top_gross_bps"], m["top_net_bps"])
                    for s in cfg.sizes_quote:
                        r = m["sizes"][s]
                        if r is None:
                            cells[(*base, s)].add(None, None)               # recorded depth cannot fill this size
                        else:
                            cells[(*base, s)].add(r["gross_bps"], r["net_bps"])
    for c in cells.values():
        c.close()

    instruments = []
    for inst in sorted(books_by_inst):
        pairs = []
        venues = sorted({k.split(":", 1)[0] for k in books_by_inst[inst]})
        for va in venues:
            for vb in venues:
                if va == vb:
                    continue
                entry = {"buy_venue": va, "sell_venue": vb, "fee_bps_total": (cfg.fees[va] + cfg.fees[vb]) * 10000,
                         "by_size": {}}
                for s in ("top", *cfg.sizes_quote):
                    c = cells.get((inst, va, vb, s))
                    if c is None:
                        entry["by_size"][str(s)] = None
                        continue
                    entry["by_size"][str(s)] = {
                        "eligible_instants": c.n, "insufficient_depth": c.insufficient,
                        "gross_bps": _dist(c.gross), "net_bps": _dist(c.net),
                        "episodes_gross_positive": _episodes(c.runs_gross, interval),
                        "episodes_net_positive": _episodes(c.runs_net, interval)}
                pairs.append(entry)
        instruments.append({"instrument": inst, "books": sorted(books_by_inst[inst]), "pairs": pairs})

    from bot.quant_core.provenance import code_state
    code = code_state()
    now = datetime.now(timezone.utc)
    iso = lambda ns: datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat() if ns else None
    report = {
        "kind": REPORT_KIND, "study_version": STUDY_VERSION, "classification": CLASSIFICATION, "governance": GOVERNANCE,
        "limitations": list(LIMITATIONS), "generated_at": now.isoformat(), "runtime_s": round(time.time() - t0, 1),
        "session": {"session_id": session_id, "status": meta.get("status"), "recorder_version": meta.get("recorder_version"),
                    "schema_version": meta.get("schema_version"), "config_id": meta.get("config_id"),
                    "sample_interval_s": interval, "depth": (meta.get("config") or {}).get("depth")},
        "input": {"sha256": digest.hexdigest(), "instants": n_instants, "first_sample_utc": iso(first_utc),
                  "last_sample_utc": iso(last_utc), "pauses_over_max_gap": breaks,
                  "note": "observation files read only up to their size when the study started"},
        "config": cfg.as_dict(),
        "code": {"commit": code.commit, "dirty": code.dirty},
        "data_quality": {k: {"usable_rows": usable_rows[k], "excluded": dict(excluded[k])}
                         for k in sorted(set(usable_rows) | set(excluded))},
        "instruments": instruments,
    }
    report["run_id"] = hashlib.sha256(json.dumps({k: report[k] for k in ("input", "config", "code", "study_version")},
                                                 sort_keys=True, default=str).encode()).hexdigest()[:16]
    return report


def save_report(report: dict, out_dir: str) -> Path:
    d = Path(out_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"][:19].replace(":", "").replace("-", "")
    path = d / f"gap_study_{report['session']['session_id']}_{stamp}.json"
    with open(path, "x", encoding="utf-8") as fh:                     # never overwrite an earlier study
        json.dump(report, fh, indent=1)
    return path


def load_latest_report(out_dir: str) -> Optional[dict]:
    d = Path(out_dir) / "reports"
    paths = sorted(d.glob("gap_study_*.json"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None
