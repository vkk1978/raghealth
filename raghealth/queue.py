"""Fix queue: turn scan findings into an actionable job.

`raghealth scan --fix-queue queue.json` writes a machine-readable list of
actions (reembed / delete / ingest) that a pipeline can consume directly —
so the scan ends in a fix, not just a diagnosis.

Queue format (stable, versioned):

{
  "version": 1,
  "generated_at": "...",
  "store": "pgvector",
  "actions": [
    {"action": "reembed", "source_path": "policies/refund.md",
     "chunk_ids": ["b0", "b1"], "reason": "stale", "priority": "critical",
     "detail": "...", "data": {"max_lag_days": 43.2}},
    {"action": "delete",  "chunk_ids": ["c0"], "source_path": "...",
     "reason": "orphaned", "priority": "critical"},
    {"action": "ingest",  "source_path": "legal/terms.md",
     "reason": "never_ingested", "priority": "warning"}
  ],
  "summary": {"reembed": 2, "delete": 1, "ingest": 1, "chunks_affected": 3}
}
"""
from __future__ import annotations

import json
from collections import Counter

from .models import HealthReport, Severity

_MAP = {
    "staleness": ("reembed", "stale"),
    "orphans": ("delete", "orphaned"),
    "coverage": ("ingest", "never_ingested"),
}


def build_fix_queue(report: HealthReport) -> dict:
    actions: list[dict] = []
    for result in report.results:
        if result.check not in _MAP:
            continue
        action, reason = _MAP[result.check]
        for f in result.findings:
            if result.check == "orphans" and not f.source_path:
                continue  # "no source link" finding isn't deletable — it's a metadata gap
            entry = {
                "action": action,
                "reason": reason,
                "priority": f.severity.value,
                "source_path": f.source_path,
                "detail": f.detail,
            }
            if f.chunk_ids:
                entry["chunk_ids"] = f.chunk_ids
            if f.data:
                entry["data"] = {k: v for k, v in f.data.items()
                                 if isinstance(v, (str, int, float, bool))}
            actions.append(entry)

    # conflicts from the duplicates check become review items, not auto-actions
    for result in report.results:
        if result.check != "duplicates":
            continue
        for f in result.findings:
            if f.severity == Severity.CRITICAL:
                actions.append({
                    "action": "review_conflict",
                    "reason": "conflicting_versions",
                    "priority": "critical",
                    "chunk_ids": f.chunk_ids,
                    "detail": f.detail,
                    "data": {"similarity": f.data.get("similarity"),
                             "source_a": f.data.get("source_a"),
                             "source_b": f.data.get("source_b")},
                })

    counts = Counter(a["action"] for a in actions)
    chunks = {cid for a in actions for cid in a.get("chunk_ids", [])}
    order = {"critical": 0, "warning": 1, "info": 2}
    actions.sort(key=lambda a: order.get(a["priority"], 9))
    return {
        "version": 1,
        "generated_at": report.scanned_at.isoformat(),
        "store": report.store_name,
        "actions": actions,
        "summary": {**dict(counts), "chunks_affected": len(chunks)},
    }


def render_queue_json(report: HealthReport) -> str:
    return json.dumps(build_fix_queue(report), indent=2)
