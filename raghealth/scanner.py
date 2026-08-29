"""Scan orchestration: pull chunks + sources once, link them, run all checks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .connectors.base import SourceConnector, VectorStoreConnector
from .checks import staleness, orphans, duplicates
from .matching import PathResolver
from .models import HealthReport


def scan(store: VectorStoreConnector,
         source: SourceConnector,
         grace_days: float = 7.0,
         duplicate_threshold: float = 0.97,
         include_embeddings: bool = True,
         assume_embedded_at: Optional[datetime] = None,
         redact: bool = False,
         canary_set=None,
         embedder=None,
         limit: Optional[int] = None) -> HealthReport:
    chunks = list(store.fetch_chunks(include_embeddings=include_embeddings,
                                     limit=limit))
    sources = list(source.fetch_documents())

    # Link chunk source paths to canonical source doc paths (fuzzy).
    resolver = PathResolver([s.path for s in sources])
    for ch in chunks:
        if not ch.source_path:
            continue
        matched, method = resolver.resolve(ch.source_path)
        if matched:
            ch.metadata["_original_source_path"] = ch.source_path
            ch.metadata["_link_method"] = method
            ch.source_path = matched

    if redact:
        for ch in chunks:
            ch.content = None

    change_describer = getattr(source, "describe_change", None)
    results = [
        staleness.run(chunks, sources, grace_days=grace_days,
                      assume_embedded_at=assume_embedded_at,
                      change_describer=change_describer),
        orphans.run(chunks, sources),
        orphans.run_coverage(chunks, sources),
    ]
    if include_embeddings:
        results.append(duplicates.run(chunks, threshold=duplicate_threshold))

    if canary_set is not None and embedder is not None:
        from .canary import apply_blast_radius, run_canaries
        canary_results = run_canaries(store, embedder, canary_set)
        results.append(apply_blast_radius(results, canary_results))

    ls = resolver.stats
    return HealthReport(
        scanned_at=datetime.now(timezone.utc),
        store_name=store.name,
        source_name=source.name,
        total_chunks=len(chunks),
        total_sources=len(sources),
        results=results,
        link_stats={
            "distinct_paths": ls.total,
            "matched": ls.matched,
            "rate_pct": ls.rate,
            "by_method": dict(ls.by_method),
            "ambiguous": ls.ambiguous[:20],
            "unmatched": ls.unmatched[:20],
        },
    )
