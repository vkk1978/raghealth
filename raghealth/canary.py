"""Canary queries and blast-radius scoring.

Canaries are canonical questions your knowledge base must answer well
("what is our refund window?"). raghealth embeds each canary with YOUR
embedding model, retrieves top-k from the store, and:

  1. `canary baseline` — snapshots the results.
  2. `canary check`    — re-runs and measures overlap vs the baseline.
     Healthy systems keep 85-95% overlap; a drop means retrieval drift.
  3. During `scan --canaries` — computes BLAST RADIUS: which stale or
     orphaned chunks are actually being retrieved, and at what rank.
     A stale chunk nobody retrieves is housekeeping; a stale chunk at
     rank 1 for a common question is actively poisoning answers.

Canary file (canaries.yaml):

    k: 5
    canaries:
      - id: refund-window
        query: "How many days do customers have to request a refund?"
      - id: vacation-days
        query: "How many vacation days do employees get?"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import yaml

from .connectors.base import VectorStoreConnector
from .embedders import Embedder
from .models import CheckResult, Finding, SearchHit, Severity


@dataclass
class Canary:
    id: str
    query: str


@dataclass
class CanarySet:
    canaries: list[Canary]
    k: int = 5

    @classmethod
    def load(cls, path: str) -> "CanarySet":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        canaries = [Canary(id=c["id"], query=c["query"])
                    for c in raw.get("canaries", [])]
        if not canaries:
            raise ValueError(f"{path}: no canaries defined")
        return cls(canaries=canaries, k=int(raw.get("k", 5)))


def run_canaries(store: VectorStoreConnector, embedder: Embedder,
                 cset: CanarySet) -> dict[str, list[SearchHit]]:
    if not store.supports_search:
        raise RuntimeError(f"store '{store.name}' does not support search")
    return {c.id: store.search(embedder(c.query), k=cset.k)
            for c in cset.canaries}


# ------------------------------------------------------------- baseline ----
def baseline_payload(cset: CanarySet,
                     results: dict[str, list[SearchHit]]) -> dict:
    return {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "k": cset.k,
        "canaries": {
            c.id: {
                "query": c.query,
                "results": [{"chunk_id": h.chunk_id, "score": round(h.score, 4),
                             "source_path": h.source_path}
                            for h in results[c.id]],
            } for c in cset.canaries
        },
    }


@dataclass
class CanaryDrift:
    canary_id: str
    query: str
    overlap_pct: float
    missing: list[str] = field(default_factory=list)   # in baseline, gone now
    new: list[str] = field(default_factory=list)       # now, not in baseline


def check_against_baseline(baseline: dict,
                           results: dict[str, list[SearchHit]]) -> list[CanaryDrift]:
    drifts = []
    for cid, base in baseline.get("canaries", {}).items():
        base_ids = [r["chunk_id"] for r in base["results"]]
        cur_ids = [h.chunk_id for h in results.get(cid, [])]
        base_set, cur_set = set(base_ids), set(cur_ids)
        overlap = (100.0 * len(base_set & cur_set) / len(base_set)
                   if base_set else 100.0)
        drifts.append(CanaryDrift(
            canary_id=cid, query=base.get("query", ""),
            overlap_pct=round(overlap, 1),
            missing=[i for i in base_ids if i not in cur_set],
            new=[i for i in cur_ids if i not in base_set]))
    return drifts


# ---------------------------------------------------------- blast radius ----
@dataclass
class Exposure:
    """How retrievable a chunk is across the canary set."""
    hits: list[tuple[str, int, float]] = field(default_factory=list)  # (canary, rank, score)

    @property
    def best_rank(self) -> int:
        return min(r for _, r, _ in self.hits)

    @property
    def label(self) -> str:
        cid, rank, _ = min(self.hits, key=lambda h: h[1])
        return f"retrieved by canary '{cid}' at rank {rank}"


def compute_exposure(results: dict[str, list[SearchHit]]) -> dict[str, Exposure]:
    exposure: dict[str, Exposure] = {}
    for cid, hits in results.items():
        for rank, h in enumerate(hits, start=1):
            exposure.setdefault(h.chunk_id, Exposure()).hits.append(
                (cid, rank, h.score))
    return exposure


def apply_blast_radius(check_results: list[CheckResult],
                       results: dict[str, list[SearchHit]]) -> CheckResult:
    """Annotate staleness/orphan findings with exposure; escalate severities;
    return a new 'blast_radius' CheckResult summarizing active poisoning."""
    exposure = compute_exposure(results)
    flagged_exposed: list[tuple[Finding, str, Exposure]] = []

    for cr in check_results:
        if cr.check not in ("staleness", "orphans"):
            continue
        for f in cr.findings:
            hit_ids = [cid for cid in f.chunk_ids if cid in exposure]
            if not hit_ids:
                f.data["blast_radius"] = "not retrieved by any canary"
                continue
            worst = min(hit_ids, key=lambda cid: exposure[cid].best_rank)
            exp = exposure[worst]
            f.data["blast_radius"] = exp.label
            f.data["exposed_chunk_ids"] = hit_ids
            f.detail += f" ⚠ ACTIVE: {len(hit_ids)} of these chunks are {exp.label}."
            if exp.best_rank <= 3:
                f.severity = Severity.CRITICAL
            flagged_exposed.append((f, cr.check, exp))

    findings = []
    for f, check, exp in sorted(flagged_exposed, key=lambda t: t[2].best_rank):
        findings.append(Finding(
            check="blast_radius",
            severity=Severity.CRITICAL if exp.best_rank <= 3 else Severity.WARNING,
            title=f"{check} · {f.source_path or f.title} — {exp.label}",
            detail=("This flagged content is not just sitting in the index; "
                    "it is being served for canonical questions right now. "
                    "Fix these first."),
            chunk_ids=f.data.get("exposed_chunk_ids", []),
            source_path=f.source_path,
            data={"best_rank": exp.best_rank},
        ))

    n_queries = len(results)
    n_exposed = len(flagged_exposed)
    summary = (f"Ran {n_queries} canary quer{'ies' if n_queries != 1 else 'y'}: "
               + (f"{n_exposed} flagged finding(s) are ACTIVELY retrieved — "
                  f"prioritize these." if n_exposed else
                  "no stale/orphaned content appears in canary results."))
    return CheckResult(check="blast_radius", summary=summary, findings=findings,
                       stats={"canaries": n_queries, "exposed_findings": n_exposed})


def save_json(payload: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
