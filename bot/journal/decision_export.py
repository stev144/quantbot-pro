# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/decision_export.py
#
# claude code changed: new file — 2026-10-07, Trading Decision Journal Phase 1. OD-J5 (owner): the database is
# the CANONICAL journal; this compressed CSV + provenance sidecar is a research/export format ONLY. The sidecar
# is written by the existing bot.quant_core.provenance.write_research_output_provenance (no second provenance
# system); an export never overwrites an existing file.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import pandas as pd

EXPORT_KIND = "DECISION_STREAM"
_JSON_COLUMNS = ("reason_detail", "lifecycle_stages", "gate_vector", "estimated_cost", "risk_state",
                 "portfolio_state", "liquidity_state", "financing_state", "data_quality_state")


def export_run(run, out_dir) -> Path:
    """Write <out_dir>/<run_id>_decisions.csv.gz plus its provenance sidecar; returns the CSV path."""
    from bot.journal.models import DecisionEvent
    from bot.quant_core.provenance import CodeState, write_research_output_provenance
    if run.status == "OPEN":
        raise ValueError(f"run {run.run_id} is still OPEN; export a closed run")
    out = Path(out_dir) / f"{run.run_id}_decisions.csv.gz"
    rows = []
    for d in DecisionEvent.objects.filter(run=run).order_by("candle_index").select_related("snapshot"):
        r = {f.attname: getattr(d, f.attname) for f in DecisionEvent._meta.concrete_fields}
        r["run_id"] = run.run_id
        r.pop("run_id_id", None)
        for c in _JSON_COLUMNS:
            r[c] = json.dumps(r[c], sort_keys=True)
        snap = getattr(d, "snapshot", None)
        r["snapshot_kind"] = snap.snapshot_kind if snap else ""
        r["input_window_sha256"] = snap.input_window_sha256 if snap else ""
        r["snapshot_values"] = json.dumps(snap.values, sort_keys=True) if snap else ""
        rows.append(r)
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "xb") as fh:                       # "x": an earlier export is never overwritten
        fh.write(gzip.compress(buf.getvalue().encode("utf-8"), mtime=0))
    side = write_research_output_provenance(
        out, kind=EXPORT_KIND, producer="bot.journal.decision_export",
        inputs={"scheme": "QBP-DATAV1", "version": run.data_version, "symbols": [run.symbol]},
        config={"journal_run_id": run.run_id, "journal_run_status": run.status, "environment": run.environment,
                "execution_mode": run.execution_mode, "taxonomy_version": run.taxonomy_version,
                "schema_version": run.schema_version, "gate_order_version": run.gate_order_version,
                "feature_set_version": run.feature_set_version, "run_config_hash": run.config_hash,
                "n_decisions": len(rows), "canonical_store": "database (OD-J5); this file is an export"},
        code=CodeState(run.code_commit, run.code_dirty, run.dirty_digest))
    if side is None:
        raise RuntimeError(f"provenance sidecar was not written for {out}; the export is incomplete")
    return out


def read_export(path):
    """(DataFrame with JSON columns decoded, OutputProvenance). Identifiers are read as text (never re-typed)."""
    from bot.quant_core.provenance import read_provenance
    df = pd.read_csv(path, dtype={"decision_id": str, "run_id": str, "backtest_trade_id": str},
                     keep_default_na=False, na_values=[""])
    for c in _JSON_COLUMNS:
        df[c] = df[c].map(json.loads)
    return df, read_provenance(path)
