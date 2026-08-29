# The benchmark nobody runs: 26 weeks of knowledge-base churn

*Part 4 of a series on benchmarking vector databases. Parts 1–3 measured
what everyone measures — latency, recall, memory, build time. This part
measures what happens after week one.*

---

Every vector database benchmark, including mine, shares an unstated
assumption: load the vectors once, query a frozen index. That's a fair way
to compare engines. It is not how any production knowledge base behaves.

Real corpora churn. Documents get edited and re-embedded. Documents get
deleted. New ones arrive. And — the part that motivated this experiment —
some of that activity never propagates to the index: an edit that nobody
re-ingested, a deletion that removed the file but not its vectors.

So I extended the benchmark harness to simulate 26 weeks of realistic
weekly activity against three databases (Chroma, Qdrant, pgvector — the
same abstract-base-class design from part 2), and measured two families of
metrics side by side each week.

## The setup

Starting corpus: 300 documents × 4 chunks = 1,200 vectors (48 dims). Small
on purpose — this experiment is about the *shape* of curves under churn,
not absolute performance, and at this scale every engine retrieves
near-perfectly, which turns out to sharpen the finding rather than weaken
it.

Every simulated week, deterministically seeded:

- **8 documents edited and re-ingested** — healthy churn: old chunks
  deleted, new revisions embedded
- **2 documents edited but NOT re-ingested** — the staleness failure mode
- **2 documents deleted from the source, vectors left behind** — the orphan
  failure mode
- **3 new documents added and ingested; 1 added but never ingested** — the
  coverage failure mode

Each week I measured **performance** (recall@10 against brute-force ground
truth recomputed over current contents; p50/p95 query latency over 150
fixed queries) and **health** (freshness score — the percentage of chunks
that are current and linked to a live source; stale and orphaned chunk
counts; and top-5 overlap for 8 fixed "canary" queries against their week-0
results). Health was measured with raghealth, an open-source
knowledge-base health tool I maintain; the same checks are implementable
by hand — they're metadata comparisons.

## The results

Performance, across all three databases, over all 26 weeks:

| metric | week 1 | week 26 |
|---|---|---|
| recall@10 | 0.998–1.000 | 0.998–1.000 |
| p95 latency | ~1 ms | ~1 ms |

Flat. The engines were completely untroubled by six months of churn —
inserts, deletes, and re-upserts at this scale left no measurable mark on
retrieval quality or speed. (Worth testing at 100K+ with HNSW tombstone
accumulation, which is future work; at this scale, nothing.)

Health, over the same 26 weeks:

| metric | week 1 | week 26 |
|---|---|---|
| freshness score | 98.7% | **76.7%** |
| stale chunks | 8 | **156** |
| orphaned chunks | 8 | **208** |
| canary overlap vs week 0 | 98% | **80%** |

By week 26, nearly a quarter of the index was either embedded from a
superseded revision or pointing at a document that no longer exists — and
one in five canary queries' top results had changed, stepwise, as the
documents behind them were edited or orphaned.

Two observations from the data worth pulling out.

**The health curves are identical across all three databases.** Chroma,
Qdrant, and pgvector produced the same freshness score, the same stale and
orphan counts, the same canary overlap, week after week. Obvious in
retrospect, clarifying to see measured: knowledge decay is a property of
the *content and the pipeline*, not the engine. Choosing a better vector
database does not help with this problem at all, which also means engine
benchmarks — mine included — cannot inform you about it.

**Canary overlap decays in steps, not gradually.** It sat at 98% for nine
weeks, then dropped when specific canary-relevant documents were edited or
orphaned. That's the realistic signature: retrieval drift is event-driven.
A weekly overlap check catches the event the week it happens; nothing else
in the metrics stack notices at all.

## What this means

If you monitor a RAG system today, you almost certainly monitor the top
half of this experiment: latency, error rates, maybe recall on a test set.
This experiment's entire point is that those metrics are structurally
incapable of detecting the bottom half. A knowledge base can lose twenty
points of freshness — hundreds of chunks serving superseded or deleted
content — while every performance chart stays perfectly flat, because
similarity search does not have a concept of "current."

The checks that do detect it are unglamorous: compare each chunk's
ingestion timestamp against its source's modification time; verify each
chunk's source still exists; run a fixed set of canonical queries weekly
and diff the top-k. All metadata bookkeeping, no ML. I've packaged them in
raghealth (MIT — GITHUB_URL), which was also the measurement instrument
here, but the experiment is reproducible with or without it: the harness
is in the repo under `benchmark/`, seeded and deterministic
(`python benchmark/churn_experiment.py`).

Limitations, stated plainly: synthetic vectors and small scale mean the
*performance* rows above shouldn't be generalized — that's what parts 1–3
and ann-benchmarks are for. The churn rates are assumptions (roughly 4%
weekly document activity with a quarter of edits failing to propagate);
your pipeline's real propagation failure rate is the number that actually
matters, and the useful takeaway is that it's cheap to start measuring it.

*Reproduce it: `git clone GITHUB_URL && python benchmark/churn_experiment.py`
— runs in a few minutes on a laptop against all three databases.*
