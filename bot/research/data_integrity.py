# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/data_integrity.py
#
# claude code changed: new file — 2026-10-03, remediation F-09 (partial last
# candle). Detects stored candles that were still FORMING when written, and
# builds a corrected, versioned dataset WITHOUT silently overwriting history.
#
# CANONICAL COMPLETED-CANDLE RULE
#   A candle with open time T and interval Δ closes at T+Δ (Binance reports
#   close_time = T+Δ−1 ms). It is complete only if it was RETRIEVED after it
#   closed: t_fetch >= T+Δ. Going forward data_fetcher._build_ohlcv_dataframe
#   enforces this at fetch time (close_time < t_fetch). For files already on
#   disk t_fetch is unknown; the file's write time (mtime) is an UPPER bound on
#   it, so:
#     PARTIAL       T+Δ > mtime                    — certainly fetched before close
#     UNVERIFIABLE  T+Δ <= mtime < T+Δ+margin      — fetch may have preceded close
#     COMPLETE      mtime >= T+Δ+margin
#   PARTIAL and UNVERIFIABLE last rows are both dropped (strict choice: a row
#   that may have been fetched before its close is never kept as final).
#   The rule depends only on time (never on the candle's prices), so dropping
#   a PARTIAL row is a deterministic boundary rule, not a data-dependent
#   selection.
#
# VERSIONING (no silent overwrite)
#   v1 = the dataset as fingerprinted in data_manifest_CRRP-2026-10-02.json.
#   For each PARTIAL file: the original is copied byte-identical to
#   data/_archive/v1_CRRP-2026-10-02/, then the live file is rewritten as the
#   original bytes minus its final line (every other row byte-identical).
#   A v2 manifest records, per file, the v1 and v2 SHA-256, the rule, the
#   dropped row verbatim, and the status of every file.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import csv
import glob
import hashlib
import io
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

UNVERIFIABLE_MARGIN_SECONDS = 60
ARCHIVE_DIR = os.path.join("data", "_archive", "v1_CRRP-2026-10-02")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_ts(value) -> pd.Timestamp:
    s = str(value).strip()
    if s.isdigit():
        v = int(s)
        return pd.to_datetime(v, unit="ms" if v > 1e11 else "s", utc=True)
    return pd.to_datetime(s, utc=True)


def _interval_of(path: str) -> Optional[pd.Timedelta]:
    name = os.path.basename(path)
    for tag, delta in (("_1h", "1h"), ("_4h", "4h"), ("_1d", "1D")):
        if tag in name:
            return pd.Timedelta(delta)
    return None


def _split_last_line(raw: bytes):
    """(body_without_last_line, last_line) — body keeps its trailing newline."""
    stripped = raw.rstrip(b"\r\n")
    cut = max(stripped.rfind(b"\n"), -1)
    return raw[:cut + 1], stripped[cut + 1:]


def last_candle_status(path: str, mtime: Optional[float] = None,
                       margin_seconds: int = UNVERIFIABLE_MARGIN_SECONDS) -> Dict:
    """Classify the final candle of one candle file (see module docstring)."""
    interval = _interval_of(path)
    if interval is None:
        return {"file": path, "status": "NOT_A_CANDLE_FILE"}
    with open(path, "rb") as f:
        raw = f.read()
    _, last = _split_last_line(raw)
    first_field = next(csv.reader(io.StringIO(last.decode("utf-8"))))[0]
    open_ts = _parse_ts(first_field)
    close_ts = open_ts + interval
    written = datetime.fromtimestamp(os.path.getmtime(path) if mtime is None else mtime, timezone.utc)
    if close_ts > written:
        status = "PARTIAL"
    elif written < close_ts + pd.Timedelta(seconds=margin_seconds):
        status = "UNVERIFIABLE"
    else:
        status = "COMPLETE"
    return {"file": path.replace("\\", "/"), "status": status, "last_open": str(open_ts), "last_close": str(close_ts),
            "written": written.isoformat(), "last_row": last.decode("utf-8")}


def audit(paths: Optional[List[str]] = None) -> List[Dict]:
    paths = paths if paths is not None else sorted(glob.glob("data/*_1h.csv") + glob.glob("data/forex/*_1h.csv"))
    return [last_candle_status(p) for p in paths]


def _archive_path(p: str, archive_dir: str, data_root: str) -> str:
    """Archive location of file p: its path relative to data_root when p lives
    under it (data/forex/X.csv -> <archive>/forex/X.csv), else its basename —
    never a path that escapes archive_dir."""
    root = os.path.abspath(data_root)
    ap = os.path.abspath(p)
    rel = os.path.relpath(ap, root) if os.path.commonpath([ap, root]) == root else os.path.basename(ap)
    return os.path.join(archive_dir, rel)


def build_corrected_version(manifest_out: str, paths: Optional[List[str]] = None, archive_dir: str = ARCHIVE_DIR,
                            dry_run: bool = False, data_root: str = "data") -> Dict:
    """Archive and correct every PARTIAL file; write the v2 manifest. Idempotent:
    a file whose archived v1 copy already exists is not archived twice."""
    statuses = audit(paths)
    entries = []
    for st in statuses:
        p = st["file"]
        e = dict(st, v1_sha256=_sha256(p))
        if st["status"] in ("PARTIAL", "UNVERIFIABLE"):   # strict: a candle that may have been fetched before its close is not kept
            arch = _archive_path(p, archive_dir, data_root)
            if not dry_run:
                os.makedirs(os.path.dirname(arch), exist_ok=True)
                if not os.path.exists(arch):
                    shutil.copy2(p, arch)                       # byte-identical v1 copy (mtime preserved)
                assert _sha256(arch) == e["v1_sha256"], f"archive copy differs from v1 for {p}"
                with open(p, "rb") as f:
                    raw = f.read()
                body, _ = _split_last_line(raw)
                with open(p, "wb") as f:
                    f.write(body)                               # every other row byte-identical
            e["action"] = "LAST_ROW_DROPPED" if st["status"] == "PARTIAL" else "DROPPED_UNVERIFIABLE"
            e["archived_v1"] = arch.replace("\\", "/")
            e["dropped_row"] = st["last_row"]
        else:
            e["action"] = "UNCHANGED"
        e["v2_sha256"] = e["v1_sha256"] if dry_run else _sha256(p)
        entries.append(e)
    out = {
        "dataset_version": "v2", "parent_version": "v1 (data_manifest_CRRP-2026-10-02.json)",
        "reset_point": "CRRP-2026-10-02", "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule": "candle complete iff fetched after its close (t_fetch >= open + interval); stored files: PARTIAL if "
                "open+interval > file mtime, UNVERIFIABLE if within %ds after close" % UNVERIFIABLE_MARGIN_SECONDS,
        "scope": "data/*_1h.csv and data/forex/*_1h.csv (funding/OI event files are not candles)",
        "archive_dir": archive_dir.replace("\\", "/"), "dry_run": dry_run,
        "counts": {s: sum(1 for e in entries if e["status"] == s) for s in ("PARTIAL", "UNVERIFIABLE", "COMPLETE")},
        "files": entries,
    }
    if not dry_run:
        with open(manifest_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, indent=1)
            f.write("\n")
    return out
