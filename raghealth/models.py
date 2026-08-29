"""Core data models for raghealth."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Chunk:
    """A single chunk stored in a vector database."""

    id: str
    source_path: Optional[str] = None      # link back to the source document
    content: Optional[str] = None          # text content (may be truncated)
    embedding: Optional[list[float]] = None
    embedded_at: Optional[datetime] = None  # when the embedding was created
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_days(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.embedded_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.embedded_at).total_seconds() / 86400.0


@dataclass
class SourceDoc:
    """A document in the source-of-truth system (filesystem, Notion, Drive...)."""

    path: str                              # canonical identifier, matches Chunk.source_path
    title: Optional[str] = None
    last_modified: Optional[datetime] = None
    exists: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    """One retrieval result from a vector store similarity search."""

    chunk_id: str
    score: float                           # similarity, higher = closer
    source_path: Optional[str] = None
    content: Optional[str] = None


@dataclass
class Finding:
    """One issue surfaced by a check."""

    check: str                             # "staleness" | "orphans" | "duplicates" | "coverage"
    severity: Severity
    title: str
    detail: str
    chunk_ids: list[str] = field(default_factory=list)
    source_path: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    """Aggregate result of one check across the knowledge base."""

    check: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)


@dataclass
class HealthReport:
    """Full scan output."""

    scanned_at: datetime
    store_name: str
    source_name: str
    total_chunks: int
    total_sources: int
    results: list[CheckResult] = field(default_factory=list)
    link_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def freshness_score(self) -> float:
        """0-100. Percentage of chunks that are NOT stale/orphaned.

        The headline number: 'your knowledge base is 66% healthy'.
        """
        if self.total_chunks == 0:
            return 100.0
        bad_ids: set[str] = set()
        for r in self.results:
            if r.check in ("staleness", "orphans"):
                for f in r.findings:
                    bad_ids.update(f.chunk_ids)
        return round(100.0 * (1 - len(bad_ids) / self.total_chunks), 1)

    @property
    def total_findings(self) -> int:
        return sum(len(r.findings) for r in self.results)
