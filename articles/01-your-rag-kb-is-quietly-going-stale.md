# Your RAG knowledge base is quietly going stale

*A technical explanation of why retrieval-augmented systems drift out of date
without any visible failure, and what to check for.*

---

Retrieval-augmented generation has a property that took many teams a while to
notice: the retrieval index and the source of truth are two separate systems,
and nothing keeps them synchronized by default.

Your documents live in Notion, Confluence, a git repo, Google Drive. Your
embeddings live in pgvector, Chroma, Qdrant, Pinecone. The pipeline that
connects them ran at ingestion time. Unless you built explicit
re-synchronization — and most teams building their first RAG system did not —
every edit, deletion, and addition on the source side after that moment is
invisible to retrieval.

This produces four distinct failure modes. They're worth naming separately,
because they have different causes and different fixes.

## 1. Stale chunks

Someone updates the refund policy from 14 days to 30. The document changes;
the chunks embedded from the old version don't. Here's the part that makes
this a genuine engineering problem rather than an oversight: **semantic
similarity has no temporal dimension.** The old chunk is still an excellent
semantic match for "what is the refund window?" — arguably a better match
than surrounding text in the new version. It will keep being retrieved, and
retrieved confidently. There is no error, no low similarity score, no signal.

## 2. Orphaned chunks

A document gets deleted or archived — an old API version's docs, a superseded
procedure, a discontinued product's terms. Deleting the document does not
delete its chunks unless your pipeline explicitly propagates deletions, and
many don't. The result is content that no longer exists anywhere in your
organization but is still retrievable and citable by your assistant. In
document-management terms, your index is retaining obsolete documents at a
point of use.

## 3. Conflicting versions

The subtler variant: the new version of a document gets ingested, but the old
version's chunks were never removed. Now two near-identical chunks with
different facts — 15 vacation days and 20 vacation days — sit in the same
index with cosine similarity above 0.99. Which one the retriever returns can
vary with the exact query phrasing, top-k settings, or tie-breaking. Users
experience this as the assistant contradicting itself between sessions, which
damages trust faster than a consistent wrong answer would.

## 4. Coverage gaps

The inverse problem: a document exists in the source system but was never
ingested — it postdates the last pipeline run, lives in a folder the pipeline
doesn't watch, or failed silently during ingestion. The assistant answers "I
don't know" about knowledge the organization demonstrably has, or worse,
answers from an older document that *was* ingested.

## Why dashboards don't catch this

The uncomfortable property shared by all four modes is that standard
observability sees nothing. Latency is normal. Error rates are zero.
Similarity scores are high — that's precisely the problem. Retrieval traces,
if you have them, show plausible chunks being retrieved for every query. The
degradation is in *correctness relative to a source of truth the monitoring
stack has no connection to*.

Answer-quality evaluation (faithfulness, relevance metrics) doesn't catch it
either, for a subtle reason: a response grounded in a stale chunk is
perfectly *faithful* — to the stale chunk. The evaluation validates that the
model used its context honestly; it has no way to know the context itself is
three revisions out of date.

## What checking actually requires

The good news is that detecting all four modes is mostly a metadata problem,
not an ML problem. You need two things stored per chunk, and most pipelines
already store the first:

1. **A source identifier** — which document did this chunk come from?
2. **An ingestion timestamp** — when was it embedded?

With those, the checks are direct comparisons:

- *Stale:* source `last_modified` > chunk `embedded_at`. (For git-backed
  sources, `git log` gives you both reliable timestamps and the actual diff —
  useful for telling a typo fix from a substantive change.)
- *Orphaned:* chunk's source identifier doesn't resolve to any live document.
- *Conflicting:* pairs of chunks with very high cosine similarity (the
  vectors are already in your database — no re-embedding needed) whose source
  identifiers differ.
- *Missing:* live documents with no chunks pointing at them.

One refinement makes the results far more actionable: not every stale chunk
matters equally. A stale chunk that nothing ever retrieves is housekeeping. A
stale chunk that ranks first for a question your users ask daily is an active
problem. Running a fixed set of canonical queries against the index and
checking which flagged chunks actually appear in the results — and at what
rank — turns a list of two hundred findings into the three worth fixing this
week.

## Doing this yourself

If you want to build this in-house, the recipe is above and none of it is
exotic: store the two metadata fields at ingestion, write the four
comparisons, schedule them, and diff the results week over week. The main
engineering effort ends up in unglamorous places — matching inconsistent path
formats between your chunk metadata and your source system, handling
timestamps buried in JSON metadata, and connector code per store and source.

I built an open-source tool that packages exactly this: **raghealth**
(MIT-licensed). It connects read-only to pgvector/Supabase, Chroma, or Qdrant
on one side and filesystem/git, Notion, or Google Drive on the other, runs
the four checks plus the canary-query prioritization, and outputs a report
and a machine-readable fix queue (re-embed these, delete those, ingest
these). `pip install raghealth && raghealth demo` shows it on synthetic data
in a few seconds; there's also an interactive playground with industry
scenarios if you'd rather look before installing.

Whether you use it or build your own, the underlying point stands: a RAG
system on living content decays by default, the decay is invisible to every
metric you're currently watching, and checking for it is cheap. It's worth
knowing your number.

---

*Links: GitHub GITHUB_URL · playground PLAYGROUND_URL · demo gallery
PAGES_URL*
