# claude code changed: 2026-10-26 — new file. Evidence ladder for one Explainer concept, built ONLY from records that
# exist: the code itself (does the referenced attribute import?), research_data/ artifacts classified by the evidence
# registry (bot/research/evidence_registry.py), the strategy verdict registry and the research reset point. The six
# rungs are separate claims and are never merged:
#   1 Implemented               — the referenced code imports
#   2 Execution recorded        — an output artifact of a run exists in research_data/
#   3 Test passed               — the platform stores no test-run results, so this is always "unavailable" here
#   4 Research validation done  — at least one artifact carries a registry classification other than UNVERIFIED
#   5 Research validation passed — an artifact is CANONICAL (current evidence) — a result, not a governance record
#   6 Trading edge established  — a strategy is production-eligible in validated_feature_registry
# The AI never decides a rung; it is told the rungs as facts.

import fnmatch
import os
from typing import Dict, List

from django.conf import settings

from bot.academy.explainer.registry import Concept

UNAVAILABLE = "Evidence unavailable — this claim cannot currently be verified."
YES, NO, UNKNOWN = "yes", "no", "unavailable"


def _resolve(module: str, attr: str) -> bool:
    """Is `attr` (e.g. "CointegrationEngine._test_pair" or "FEE_RATE") defined in `module`'s source? Checked by parsing
    the file, not importing it: importing the research modules (statsmodels, scipy) cost ~8 s on a cold page load.
    bot/tests/test_explainer.py additionally imports every reference for real."""
    import ast
    import importlib.util
    try:
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            return False
        with open(spec.origin, encoding="utf-8") as fh:
            body = ast.parse(fh.read()).body
    except Exception:
        return False
    for part in attr.split("."):
        found = None
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part:
                found = node
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == part for t in node.targets):
                found = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == part:
                found = node
        if found is None:
            return False
        body = getattr(found, "body", [])
    return True


def _research_files() -> List[str]:
    root = os.path.join(settings.BASE_DIR, "research_data")
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), root).replace("\\", "/"))
    return out


def matching_artifacts(patterns, files=None) -> List[Dict]:
    """research_data/ files matching the concept's patterns, each with its evidence-registry classification."""
    from bot.research.evidence_registry import classify_artifact
    files = _research_files() if files is None else files
    out, seen = [], set()
    for pat in patterns:
        for rel in files:
            if rel not in seen and fnmatch.fnmatch(rel, pat):
                seen.add(rel)
                c = classify_artifact(rel)
                out.append({"path": rel, "class": c["class"], "label": c["label"], "reason": c["reason"],
                            "is_current_evidence": c["is_current_evidence"]})
    return out


def evidence_ladder(concept: Concept, files=None) -> Dict:
    impl = [{"ref": f"{m}:{a}", "ok": _resolve(m, a)} for m, a in concept.implementation]
    arts = matching_artifacts(concept.evidence_patterns, files) if concept.evidence_patterns else []
    classes = sorted({a["class"] for a in arts})
    classified = [a for a in arts if a["class"] != "UNVERIFIED"]
    canonical = [a for a in arts if a["is_current_evidence"]]

    from bot.research.validated_feature_registry import STRATEGY_VERDICTS
    from bot.research.evidence_registry import RESET_POINT
    eligible = [v.strategy_name for v in STRATEGY_VERDICTS.values() if v.production_eligible]

    def rung(level, status, detail):
        return {"level": level, "status": status, "detail": detail}

    rungs = [
        rung("Implemented",
             YES if impl and all(i["ok"] for i in impl) else (NO if impl else UNKNOWN),
             (f"{sum(i['ok'] for i in impl)} of {len(impl)} referenced code location(s) import."
              if impl else "No implementation is referenced for this concept.")),
        rung("Execution recorded",
             YES if arts else UNKNOWN,
             (f"{len(arts)} research output file(s) found ({', '.join(classes)})." if arts else UNAVAILABLE)),
        rung("Test passed", UNKNOWN,
             UNAVAILABLE + " The platform does not store test-run results; a test file existing is not a pass."),
        rung("Research validation completed",
             YES if classified else UNKNOWN,
             (f"{len(classified)} output(s) carry an evidence-registry classification." if classified else UNAVAILABLE)),
        rung("Research validation passed",
             YES if canonical else (NO if classified else UNKNOWN),
             (f"{len(canonical)} output(s) are CANONICAL current evidence." if canonical else
              f"No output is CANONICAL current evidence (research reset point {RESET_POINT})." if classified else
              UNAVAILABLE)),
        rung("Trading edge established",
             YES if eligible else NO,
             (f"Production-eligible strategies: {', '.join(eligible)}." if eligible else
              f"No strategy is production-eligible; no trading edge is established (reset point {RESET_POINT}).")),
    ]
    return {"rungs": rungs, "implementation": impl, "artifacts": arts[:12], "n_artifacts": len(arts),
            "classes": classes, "reset_point": RESET_POINT}


def model_evidence_text(ladder: Dict) -> str:
    """The evidence statuses as plain facts for the model (no file paths)."""
    lines = [f"- {r['level']}: {r['status'].upper()} — {r['detail']}" for r in ladder["rungs"]]
    return "Recorded evidence status (authoritative; do not contradict or extend):\n" + "\n".join(lines)
