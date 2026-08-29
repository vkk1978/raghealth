"""Orphan check: chunks whose source document no longer exists (deleted,
moved, renamed, or archived). These chunks WILL still be retrieved — the
vector index doesn't know the source is gone — so the assistant can cite
documents that no longer exist.
"""
from __future__ import annotations

from collections import defaultdict

from ..models import CheckResult, Chunk, Finding, Severity, SourceDoc


def run(chunks: list[Chunk], sources: list[SourceDoc]) -> CheckResult:
    live_paths = {s.path for s in sources if s.exists}
    archived_paths = {s.path for s in sources if not s.exists}

    orphans: dict[str, list[Chunk]] = defaultdict(list)
    no_link = 0
    for ch in chunks:
        if not ch.source_path:
            no_link += 1
        elif ch.source_path not in live_paths:
            orphans[ch.source_path].append(ch)

    findings: list[Finding] = []
    for path, chs in sorted(orphans.items(), key=lambda kv: -len(kv[1])):
        reason = "archived" if path in archived_paths else "deleted or moved"
        findings.append(Finding(
            check="orphans",
            severity=Severity.CRITICAL,
            title=f"{len(chs)} orphaned chunk(s) → '{path}'",
            detail=(f"The source document was {reason}, but its chunks are "
                    f"still in the index and still retrievable."),
            chunk_ids=[c.id for c in chs],
            source_path=path,
            data={"reason": reason},
        ))
    if no_link:
        findings.append(Finding(
            check="orphans",
            severity=Severity.WARNING,
            title=f"{no_link} chunk(s) have no source link at all",
            detail=("These chunks carry no source_path metadata, so freshness "
                    "can never be verified for them. Store a source identifier "
                    "at ingestion time."),
            data={"count": no_link},
        ))

    orphan_count = sum(len(v) for v in orphans.values())
    return CheckResult(
        check="orphans",
        summary=(f"{orphan_count} chunk(s) point at sources that no longer exist; "
                 f"{no_link} chunk(s) have no source link."),
        findings=findings,
        stats={"orphaned": orphan_count, "unlinked": no_link},
    )


def run_coverage(chunks: list[Chunk], sources: list[SourceDoc]) -> CheckResult:
    """Coverage check: live source documents that were never ingested.

    The knowledge exists, the RAG system just can't see it — users get
    'I don't know' (or worse, a confident answer from an older doc).
    """
    ingested = {c.source_path for c in chunks if c.source_path}
    missing = [s for s in sources if s.exists and s.path not in ingested]

    findings = [Finding(
        check="coverage",
        severity=Severity.WARNING,
        title=f"Never ingested: '{s.title or s.path}'",
        detail="This document exists in the source system but has no chunks in the index.",
        source_path=s.path,
    ) for s in sorted(missing, key=lambda s: s.path)]

    live = sum(1 for s in sources if s.exists)
    pct = 100.0 * (live - len(missing)) / live if live else 100.0
    return CheckResult(
        check="coverage",
        summary=(f"{live - len(missing)} of {live} live source documents are in the "
                 f"index ({pct:.0f}% coverage); {len(missing)} never ingested."),
        findings=findings,
        stats={"live_sources": live, "missing": len(missing),
               "coverage_pct": round(pct, 1)},
    )
