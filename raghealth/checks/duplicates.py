"""Duplicate / conflict check.

Finds pairs of chunks with near-identical embeddings that come from
DIFFERENT source documents (or different versions). When two versions of the
same fact live in the index, the retriever pulls both and the LLM gets
contradictory context — a documented, silent RAG failure mode.

Uses the vectors already stored in the DB (cosine similarity via numpy),
so there is zero embedding-API cost. For large collections we cap the
comparison set and note the sampling in the summary.
"""
from __future__ import annotations

import numpy as np

from ..models import CheckResult, Chunk, Finding, Severity


def run(chunks: list[Chunk],
        threshold: float = 0.97,
        max_chunks: int = 5000,
        max_findings: int = 100) -> CheckResult:
    vec_chunks = [c for c in chunks if c.embedding]
    sampled = False
    if len(vec_chunks) > max_chunks:
        step = len(vec_chunks) / max_chunks
        vec_chunks = [vec_chunks[int(i * step)] for i in range(max_chunks)]
        sampled = True

    n = len(vec_chunks)
    if n < 2:
        return CheckResult(
            check="duplicates",
            summary="No embeddings available to compare (run with include_embeddings).",
            stats={"compared": n},
        )

    M = np.asarray([c.embedding for c in vec_chunks], dtype=np.float32)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    M = M / norms

    findings: list[Finding] = []
    pair_count = 0
    cross_source = 0
    block = 512  # blockwise to bound memory: block x n similarity at a time
    for start in range(0, n, block):
        end = min(start + block, n)
        sims = M[start:end] @ M.T  # (block, n)
        for bi in range(end - start):
            i = start + bi
            row = sims[bi]
            js = np.where(row[i + 1:] >= threshold)[0] + i + 1  # upper triangle only
            for j in js:
                a, b = vec_chunks[i], vec_chunks[int(j)]
                pair_count += 1
                different_source = (a.source_path or "") != (b.source_path or "")
                if different_source:
                    cross_source += 1
                if len(findings) < max_findings:
                    sev = Severity.CRITICAL if different_source else Severity.INFO
                    kind = ("Conflicting versions across sources"
                            if different_source else "Redundant duplicate within a source")
                    data = {"similarity": float(row[int(j)]),
                            "source_a": a.source_path, "source_b": b.source_path}
                    if a.content and b.content:
                        # excerpts live in data, never in detail: the agent's
                        # push serializer drops excerpt_* keys, so content
                        # cannot leave the machine even on unredacted scans
                        data["excerpt_a"] = a.content[:80]
                        data["excerpt_b"] = b.content[:80]
                    findings.append(Finding(
                        check="duplicates",
                        severity=sev,
                        title=f"{kind} (similarity {row[int(j)]:.3f})",
                        detail=(f"sources: {a.source_path!r} vs {b.source_path!r}. "
                                + ("Retrieval may return contradictory context."
                                   if different_source else
                                   "Wastes index space and crowds out diverse results.")),
                        chunk_ids=[a.id, b.id],
                        data=data,
                    ))

    summary = (f"{pair_count} near-duplicate pair(s) at similarity ≥ {threshold} "
               f"among {n} chunks; {cross_source} pair(s) span DIFFERENT sources "
               f"(potential conflicting answers).")
    if sampled:
        summary += f" (sampled {max_chunks} of {len(chunks)} chunks)"

    return CheckResult(
        check="duplicates",
        summary=summary,
        findings=findings,
        stats={"compared": n, "duplicate_pairs": pair_count,
               "cross_source_pairs": cross_source, "threshold": threshold,
               "sampled": sampled},
    )
