"""Staleness check.

A chunk is STALE when its source document was modified AFTER the chunk was
embedded. The RAG system is serving an old version of the truth, and semantic
similarity gives no warning — the stale chunk scores just as high as a fresh
one would.

Severity model:
  - critical: source modified > grace_days after embedding (default 7)
  - warning:  source modified after embedding but within grace period
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from ..models import CheckResult, Chunk, Finding, Severity, SourceDoc


def run(chunks: list[Chunk], sources: list[SourceDoc],
        grace_days: float = 7.0,
        assume_embedded_at: Optional[datetime] = None,
        change_describer=None) -> CheckResult:
    """change_describer: optional callable (doc_path, embedded_at) -> str | None
    that summarizes what changed in the source since embedding (e.g. git diff)."""
    src_by_path = {s.path: s for s in sources}
    now = datetime.now(timezone.utc)

    stale_by_source: dict[str, list[tuple[Chunk, float]]] = defaultdict(list)
    unknown_embed_time = 0
    checked = 0

    for ch in chunks:
        if not ch.source_path or ch.source_path not in src_by_path:
            continue  # orphans handled by the orphans check
        src = src_by_path[ch.source_path]
        if src.last_modified is None:
            continue
        embedded_at = ch.embedded_at or assume_embedded_at
        if embedded_at is None:
            unknown_embed_time += 1
            continue
        checked += 1
        lag_days = (src.last_modified - embedded_at).total_seconds() / 86400.0
        if lag_days > 0:
            stale_by_source[ch.source_path].append((ch, lag_days))

    findings: list[Finding] = []
    for path, items in sorted(stale_by_source.items(),
                              key=lambda kv: -max(l for _, l in kv[1])):
        max_lag = max(lag for _, lag in items)
        src = src_by_path[path]
        sev = Severity.CRITICAL if max_lag > grace_days else Severity.WARNING
        days_ago = (now - src.last_modified).total_seconds() / 86400.0
        change_note = ""
        if change_describer is not None:
            earliest = min((c.embedded_at or assume_embedded_at) for c, _ in items)
            try:
                desc = change_describer(path, earliest)
            except Exception:
                desc = None
            if desc:
                change_note = f" What changed: {desc}."
        findings.append(Finding(
            check="staleness",
            severity=sev,
            title=f"{len(items)} stale chunk(s) from '{src.title or path}'",
            detail=(f"Source was updated {days_ago:.0f} day(s) ago, but these "
                    f"chunks were embedded {max_lag:.0f} day(s) BEFORE that "
                    f"update. Retrieval is serving the old version."
                    + change_note),
            chunk_ids=[c.id for c, _ in items],
            source_path=path,
            data={"max_lag_days": round(max_lag, 1),
                  "source_modified": src.last_modified.isoformat()},
        ))

    stale_chunks = sum(len(v) for v in stale_by_source.values())
    pct = (100.0 * stale_chunks / checked) if checked else 0.0
    summary = (f"{stale_chunks} of {checked} linked chunks "
               f"({pct:.0f}%) are stale — embedded before their source's latest edit.")
    if unknown_embed_time:
        summary += (f" {unknown_embed_time} chunk(s) have no embedded_at timestamp "
                    f"and could not be checked (add one at ingestion time, or set "
                    f"assume_embedded_at in config).")

    return CheckResult(
        check="staleness",
        summary=summary,
        findings=findings,
        stats={"checked": checked, "stale": stale_chunks,
               "stale_pct": round(pct, 1), "unknown_embed_time": unknown_embed_time},
    )
