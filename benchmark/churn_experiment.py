"""Churn experiment: what happens to a RAG knowledge base over 26 weeks of
realistic document activity — measured two ways at once.

Every published vector-DB benchmark loads vectors once and queries a frozen
index. Real knowledge bases churn: documents get edited (delete + re-embed),
deleted, added — and, crucially, some edits never propagate to the index.
This experiment simulates that weekly activity against three real databases
(Chroma, Qdrant, pgvector) and records, per simulated week:

  PERFORMANCE (what everyone measures)
    - recall@10 vs brute-force ground truth over current contents
    - p50 / p95 query latency

  HEALTH (what nobody measures)
    - raghealth freshness score
    - stale chunk count (source edited after embedding)
    - orphaned chunk count (source deleted, vectors remain)
    - canary top-k overlap vs the week-0 baseline

Weekly activity (deterministic, seeded):
    8 docs edited AND re-ingested        (healthy churn)
    2 docs edited, NOT re-ingested       (staleness injection)
    2 docs deleted, vectors left behind   (orphan injection)
    3 docs added and ingested
    1 doc added, never ingested          (coverage gap)

Scale is deliberately small (~1,200 vectors, dim 48): the finding is the
*shape* of the curves, not absolute numbers. At this scale recall is
near-perfect everywhere — which is exactly the point: every performance
metric stays flat while the knowledge decays.

Run:  python benchmark/churn_experiment.py [--weeks 26] [--backends chroma,qdrant,pgvector]
Outputs: benchmark/results/churn_results.json + churn_chart.png
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from raghealth.connectors.base import SourceConnector
from raghealth.models import SourceDoc
from raghealth.scanner import scan

DIM = 48
CHUNKS_PER_DOC = 4
INITIAL_DOCS = 300
N_QUERIES = 150
N_CANARIES = 8
K = 10
CANARY_K = 5
RESULTS_DIR = Path(__file__).parent / "results"

rng = random.Random(42)
np_rng = np.random.default_rng(42)

START = datetime.now(timezone.utc) - timedelta(weeks=27)


def week_time(week: int) -> datetime:
    return START + timedelta(weeks=week)


def unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def chunk_vec(doc_seed: int, chunk_i: int, revision: int) -> np.ndarray:
    """Deterministic vector per (doc, chunk, revision). Revisions stay in the
    same neighborhood (base + small delta) — edits move content slightly,
    like real re-embeddings of an edited paragraph."""
    g = np.random.default_rng(doc_seed * 1000 + chunk_i)
    base = g.standard_normal(DIM)
    if revision:
        gd = np.random.default_rng(doc_seed * 1000 + chunk_i + revision * 77)
        base = base + 0.15 * gd.standard_normal(DIM)
    return unit(base)


# ------------------------------------------------------------- sim state --
@dataclass
class Doc:
    doc_id: int
    revision: int = 0            # bumped on every edit
    ingested_rev: int = -1       # revision currently in the index (-1 = none)
    embedded_at: datetime | None = None
    last_modified: datetime = field(default_factory=lambda: START)
    exists: bool = True          # False = deleted from source

    @property
    def path(self) -> str:
        return f"docs/doc-{self.doc_id}.md"

    def chunk_ids(self) -> list[str]:
        return [f"{self.doc_id}-{i}" for i in range(CHUNKS_PER_DOC)]

    def vectors(self, revision: int) -> list[np.ndarray]:
        return [chunk_vec(self.doc_id, i, revision)
                for i in range(CHUNKS_PER_DOC)]


class SimSource(SourceConnector):
    name = "sim-docs"

    def __init__(self, docs: dict[int, Doc]):
        self.docs = docs

    def fetch_documents(self):
        for d in self.docs.values():
            yield SourceDoc(path=d.path, title=f"doc-{d.doc_id}",
                            last_modified=d.last_modified, exists=d.exists)


# --------------------------------------------------------------- backends --
class ChromaBackend:
    name = "chroma"

    def __init__(self, workdir: Path):
        import chromadb
        self.path = workdir / "chroma"
        self.client = chromadb.PersistentClient(path=str(self.path))
        try:
            self.client.delete_collection("churn")
        except Exception:
            pass
        self.col = self.client.create_collection(
            "churn", metadata={"hnsw:space": "cosine"})

    def upsert(self, ids, vecs, metas):
        self.col.upsert(ids=ids, embeddings=[v.tolist() for v in vecs],
                        metadatas=metas,
                        documents=[f"content {i}" for i in ids])

    def delete(self, ids):
        if ids:
            self.col.delete(ids=ids)

    def query(self, vec, k):
        res = self.col.query(query_embeddings=[vec.tolist()], n_results=k,
                             include=[])
        return res["ids"][0]

    def store(self):
        from raghealth.connectors.chroma import ChromaConnector
        return ChromaConnector(collection="churn", path=str(self.path),
                               source_path_key="source",
                               embedded_at_key="embedded_at")

    def teardown(self):
        pass


class QdrantBackend:
    name = "qdrant"

    def __init__(self, workdir: Path):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        self.path = workdir / "qdrant"
        self.client = QdrantClient(path=str(self.path))
        try:
            self.client.delete_collection("churn")
        except Exception:
            pass
        self.client.create_collection(
            "churn", vectors_config=VectorParams(size=DIM,
                                                 distance=Distance.COSINE))
        self._id = {}   # chunk_id str -> int point id
        self._next = 0

    def _pid(self, cid: str) -> int:
        if cid not in self._id:
            self._id[cid] = self._next
            self._next += 1
        return self._id[cid]

    def upsert(self, ids, vecs, metas):
        from qdrant_client.models import PointStruct
        pts = [PointStruct(id=self._pid(c), vector=v.tolist(),
                           payload={**m, "chunk_id": c, "text": f"content {c}"})
               for c, v, m in zip(ids, vecs, metas)]
        self.client.upsert("churn", pts)

    def delete(self, ids):
        pids = [self._id[c] for c in ids if c in self._id]
        if pids:
            from qdrant_client.models import PointIdsList
            self.client.delete("churn", points_selector=PointIdsList(points=pids))
        for c in ids:
            self._id.pop(c, None)

    def query(self, vec, k):
        res = self.client.query_points("churn", query=vec.tolist(), limit=k,
                                       with_payload=["chunk_id"])
        return [p.payload["chunk_id"] for p in res.points]

    def store(self):
        # re-use the live client: local-mode qdrant holds a file lock
        backend = self

        from raghealth.connectors.qdrant import QdrantConnector

        class _Conn(QdrantConnector):
            def __init__(self):  # bypass parent __init__ (no new client)
                self._client = backend.client
                self.collection = "churn"
                self.source_key = "source"
                self.embedded_at_key = "embedded_at"
                self.content_key = "text"
                self.batch = 512

            def fetch_chunks(self, include_embeddings=True, limit=None):
                for ch in super().fetch_chunks(include_embeddings, limit):
                    ch.id = ch.metadata.get("chunk_id", ch.id)
                    yield ch

            def close(self):
                pass
        return _Conn()

    def teardown(self):
        self.client.close()


class PgVectorBackend:
    name = "pgvector"
    DSN = "postgresql://rag:rag@localhost:5432/ragdb"

    def __init__(self, workdir: Path):
        import psycopg2
        self.conn = psycopg2.connect(self.DSN)
        cur = self.conn.cursor()
        cur.execute(f"""
            DROP TABLE IF EXISTS churn_bench;
            CREATE TABLE churn_bench (
              id TEXT PRIMARY KEY, content TEXT,
              embedding vector({DIM}), metadata JSONB);""")
        self.conn.commit()

    @staticmethod
    def _vs(v: np.ndarray) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    def upsert(self, ids, vecs, metas):
        cur = self.conn.cursor()
        from psycopg2.extras import execute_values
        rows = [(c, f"content {c}", self._vs(v), json.dumps(m))
                for c, v, m in zip(ids, vecs, metas)]
        execute_values(cur, """
            INSERT INTO churn_bench VALUES %s
            ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding,
              metadata = EXCLUDED.metadata""",
            rows, template="(%s,%s,%s::vector,%s::jsonb)")
        self.conn.commit()

    def delete(self, ids):
        if ids:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM churn_bench WHERE id = ANY(%s)", (ids,))
            self.conn.commit()

    def query(self, vec, k):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM churn_bench ORDER BY embedding <=> "
                    "%s::vector LIMIT %s", (self._vs(vec), k))
        return [r[0] for r in cur.fetchall()]

    def store(self):
        from raghealth.connectors.pgvector import PgVectorConnector
        return PgVectorConnector(
            dsn=self.DSN, table="churn_bench",
            columns={"id": "id", "content": "content", "embedding": "embedding",
                     "embedded_at": "metadata->>'embedded_at'",
                     "source_path": "metadata->>'source'",
                     "metadata": "metadata"})

    def teardown(self):
        self.conn.close()


BACKENDS = {"chroma": ChromaBackend, "qdrant": QdrantBackend,
            "pgvector": PgVectorBackend}


# ------------------------------------------------------------ experiment --
def run_backend(name: str, weeks: int, workdir: Path) -> dict:
    print(f"\n=== {name} ===")
    backend = BACKENDS[name](workdir)
    docs: dict[int, Doc] = {i: Doc(i) for i in range(INITIAL_DOCS)}
    next_doc_id = INITIAL_DOCS
    mirror: dict[str, np.ndarray] = {}      # ground-truth mirror of DB contents

    def ingest(d: Doc, week: int):
        vecs = d.vectors(d.revision)
        metas = [{"source": d.path,
                  "embedded_at": week_time(week).isoformat()}
                 for _ in vecs]
        backend.upsert(d.chunk_ids(), vecs, metas)
        for cid, v in zip(d.chunk_ids(), vecs):
            mirror[cid] = v
        d.ingested_rev, d.embedded_at = d.revision, week_time(week)

    # week 0: full ingest
    for d in docs.values():
        d.last_modified = week_time(0)
        ingest(d, 0)

    queries = [unit(np_rng.standard_normal(DIM)) for _ in range(N_QUERIES)]
    canaries = {f"c{i}": chunk_vec(i * 13, 0, 0) for i in range(N_CANARIES)}
    canary_base = {cid: backend.query(v, CANARY_K)
                   for cid, v in canaries.items()}

    weekly: list[dict] = []
    op_rng = random.Random(1000)

    for week in range(1, weeks + 1):
        live_ingested = [d for d in docs.values()
                         if d.exists and d.ingested_rev >= 0]
        # 8 healthy edits (edit + re-ingest)
        for d in op_rng.sample(live_ingested, 8):
            d.revision += 1
            d.last_modified = week_time(week)
            ingest(d, week)
        # 2 staleness injections (edit, no re-ingest)
        live_ingested = [d for d in docs.values()
                         if d.exists and d.ingested_rev >= 0]
        for d in op_rng.sample(live_ingested, 2):
            d.revision += 1
            d.last_modified = week_time(week)
        # 2 orphan injections (delete doc, leave vectors)
        candidates = [d for d in docs.values()
                      if d.exists and d.ingested_rev >= 0]
        for d in op_rng.sample(candidates, 2):
            d.exists = False
        # 3 new ingested docs + 1 never-ingested
        for j in range(4):
            d = Doc(next_doc_id); next_doc_id += 1
            d.last_modified = week_time(week)
            docs[d.doc_id] = d
            if j < 3:
                d.revision = 0
                ingest(d, week)

        # ---- performance measurement
        ids = list(mirror)
        M = np.stack([mirror[c] for c in ids])
        lat = []
        hits_at_k = 0
        for q in queries:
            truth = [ids[i] for i in np.argsort(-(M @ q))[:K]]
            t0 = time.perf_counter()
            got = backend.query(q, K)
            lat.append((time.perf_counter() - t0) * 1000)
            hits_at_k += len(set(truth) & set(got))
        recall = hits_at_k / (N_QUERIES * K)
        lat_arr = np.array(lat)

        # ---- health measurement (raghealth as the instrument)
        store = backend.store()
        report = scan(store, SimSource(docs), include_embeddings=False,
                      grace_days=7.0)
        store.close()
        stats = {r.check: r.stats for r in report.results}

        # ---- canary overlap vs week-0 baseline
        overlaps = []
        for cid, v in canaries.items():
            cur = backend.query(v, CANARY_K)
            base = canary_base[cid]
            overlaps.append(len(set(base) & set(cur)) / len(base))
        canary_overlap = 100.0 * float(np.mean(overlaps))

        row = {
            "week": week,
            "chunks": len(mirror),
            "recall_at_10": round(recall, 4),
            "latency_p50_ms": round(float(np.percentile(lat_arr, 50)), 3),
            "latency_p95_ms": round(float(np.percentile(lat_arr, 95)), 3),
            "freshness_score": report.freshness_score,
            "stale_chunks": stats["staleness"]["stale"],
            "orphaned_chunks": stats["orphans"]["orphaned"],
            "coverage_pct": stats["coverage"]["coverage_pct"],
            "canary_overlap_pct": round(canary_overlap, 1),
        }
        weekly.append(row)
        if week % 5 == 0 or week == 1:
            print(f"  w{week:02d} recall={recall:.3f} p95={row['latency_p95_ms']:.1f}ms "
                  f"fresh={row['freshness_score']}% stale={row['stale_chunks']} "
                  f"orphan={row['orphaned_chunks']} canary={canary_overlap:.0f}%")

    backend.teardown()
    return {"backend": name, "weeks": weekly}


def chart(results: list[dict], out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    colors = {"chroma": "#7c9cff", "qdrant": "#4ade80", "pgvector": "#ffc555"}
    for r in results:
        w = [x["week"] for x in r["weeks"]]
        c = colors.get(r["backend"], "gray")
        ax1.plot(w, [100 * x["recall_at_10"] for x in r["weeks"]],
                 color=c, label=f"{r['backend']} recall@10")
        ax2.plot(w, [x["freshness_score"] for x in r["weeks"]], color=c,
                 label=f"{r['backend']} freshness")
        ax2.plot(w, [x["canary_overlap_pct"] for x in r["weeks"]], color=c,
                 linestyle="--", alpha=0.6,
                 label=f"{r['backend']} canary overlap")
    ax1.set_ylabel("recall@10 (%)")
    ax1.set_ylim(0, 105)
    ax1.set_title("What everyone measures: retrieval performance (flat)")
    ax1.legend(loc="lower left", fontsize=8)
    ax1.grid(alpha=0.25)
    ax2.set_ylabel("%")
    ax2.set_ylim(0, 105)
    ax2.set_xlabel("simulated week")
    ax2.set_title("What nobody measures: knowledge health (decaying)")
    ax2.legend(loc="lower left", fontsize=8)
    ax2.grid(alpha=0.25)
    fig.suptitle("26 weeks of knowledge-base churn: performance vs health",
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print(f"\nchart -> {out}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--weeks", type=int, default=26)
    p.add_argument("--backends", default="chroma,qdrant,pgvector")
    args = p.parse_args()

    workdir = Path("/tmp/churnbench")
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    results = [run_backend(b.strip(), args.weeks, workdir)
               for b in args.backends.split(",")]
    (RESULTS_DIR / "churn_results.json").write_text(
        json.dumps(results, indent=2))
    chart(results, RESULTS_DIR / "churn_chart.png")


if __name__ == "__main__":
    main()
