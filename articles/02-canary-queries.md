# Canary queries: a cheap, boring way to catch retrieval drift

*A technique you can implement in an afternoon, with or without any tooling.*

---

Most RAG teams discover retrieval regressions the same way: a user reports a
bad answer, someone investigates, and the trail leads back to a change made
weeks earlier — a re-index, an embedding model upgrade, a chunking tweak, a
batch of new documents that shifted the neighborhood structure of the vector
space. The regression was live the whole time. Nothing measured it.

Canary queries are the retrieval equivalent of a smoke test, and they borrow
their logic from how data teams monitor pipelines: define a small set of
fixed, representative inputs; record the expected outputs; re-run on a
schedule; alert on divergence. Applied to RAG:

## The method

**1. Pick 10–20 canonical queries.** These should be real questions your
users actually ask, covering the topics that matter most — not synthetic
benchmark questions. If you have query logs, take the head of the
distribution. If you don't, ask the team what the assistant absolutely must
get right. Write them down in a file; they are now part of your system's
contract.

**2. Capture a baseline.** For each query, embed it *with the same model your
ingestion pipeline uses* (this matters — a different model gives you a
different vector space and meaningless results), retrieve top-k, and store
the resulting chunk IDs and scores. This snapshot represents "retrieval as it
behaves today, which we believe to be acceptable."

**3. Re-run on a schedule and measure overlap.** Weekly is a reasonable
default; after any re-index or pipeline change, immediately. The metric is
simple set overlap: what fraction of the baseline top-k still appears in
today's top-k, per query, and on average.

**4. Set a threshold and alert.** In stable systems, overlap holds high —
roughly 85–95% is typical week to week, since minor churn from new documents
is normal and healthy. A drop below ~70% on a query means its retrieval
neighborhood changed materially and someone should look. A drop across many
queries at once almost always traces to a pipeline-level change.

## What the changes mean

The diagnostic value is in *which direction* the churn goes:

- **Baseline chunks disappearing** after you deleted or re-embedded content
  is expected — verify the replacements are sensible and re-baseline.
- **Baseline chunks disappearing with no intentional change** is drift worth
  investigating: new content crowding out the right answers, or an index
  change you didn't know about.
- **New chunks appearing from documents that shouldn't be relevant** often
  reveals ingestion problems — duplicated content, a scraped page that
  shouldn't be in the corpus, or two versions of the same document competing.
- **An old or deleted document's chunks ranking above the current version**
  is the most actionable finding of all: it means users asking that canonical
  question are getting superseded information right now.

That last pattern points at the second use of canaries, which I'd argue is
more valuable than drift detection: **exposure scoring.** If you also run
staleness or orphan checks on your knowledge base (comparing chunk ingestion
timestamps against source modification times), the canary results tell you
which of those flagged chunks are *actually retrievable in practice*. A
knowledge base can have hundreds of stale chunks; typically only a handful
appear in the top-k for questions anyone asks. Severity becomes staleness ×
exposure, and your fix list shrinks from "everything" to "these five."

## Implementation notes

The whole thing is ~100 lines against any vector store:

```python
# baseline
baseline = {}
for q in canonical_queries:
    hits = store.search(embed(q), k=5)
    baseline[q] = [h.id for h in hits]

# weekly check
for q, base_ids in baseline.items():
    current = [h.id for h in store.search(embed(q), k=5)]
    overlap = len(set(base_ids) & set(current)) / len(base_ids)
    if overlap < 0.7:
        alert(q, missing=set(base_ids) - set(current),
              new=set(current) - set(base_ids))
```

Practical details that bite people: re-baseline deliberately (after verified
intentional changes), not automatically — an auto-updating baseline can't
detect gradual drift. Keep k identical between baseline and check. Version
the canary file in git next to your pipeline code, because the queries are
part of the system's specification. And treat embedding-model upgrades as a
full re-baseline event, since overlap across different embedding models is
not meaningful.

If you'd rather not maintain this yourself, it's packaged in **raghealth**,
an MIT-licensed knowledge-base health tool I built (`raghealth canary
baseline` / `raghealth canary check --fail-under 70`, plus the exposure
scoring integrated with staleness and orphan checks). But the technique is
the point — it's an afternoon of work, it's nearly free to run, and it
converts "a user complained" into "the Tuesday check flagged it."

---

*Links: GitHub GITHUB_URL · a live demo of exposure scoring on synthetic
industry scenarios: PLAYGROUND_URL*
