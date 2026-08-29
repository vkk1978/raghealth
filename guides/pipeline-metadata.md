# Make your pipeline raghealth-ready in 3 lines

raghealth needs two pieces of metadata per chunk to run all its checks:

1. **`source`** — an identifier linking the chunk back to its document
   (a path, URL, or page ID). Most pipelines already store this.
2. **`embedded_at`** — an ISO timestamp of when the chunk was embedded.
   Most pipelines don't store this — and it's the three-line fix below.

Without `embedded_at`, staleness detection falls back to a single assumed
date (`scan.assume_embedded_at` in raghealth.yaml) — useful, but per-chunk
timestamps are what make the check precise. Everything below is additive:
it changes nothing about retrieval, and existing chunks are unaffected
(re-ingest them over time to bring them under coverage).

---

## LangChain

Wherever you build your `Document` objects, stamp the metadata before
adding to the vector store:

```python
from datetime import datetime, timezone

def stamp(docs, source_path):                                    # line 1
    now = datetime.now(timezone.utc).isoformat()                 # line 2
    for d in docs:
        d.metadata.update(source=source_path, embedded_at=now)   # line 3
    return docs

# your existing flow, unchanged:
chunks = splitter.split_documents(loader.load())
vector_store.add_documents(stamp(chunks, source_path="docs/refund-policy.md"))
```

Works identically for `PGVector`, `Chroma`, and `QdrantVectorStore` — all
three persist `Document.metadata` (PGVector stores it in the `cmetadata`
JSONB column, which `raghealth init` auto-detects).

If you use loaders that already set `metadata["source"]` (most file and web
loaders do), you only need the `embedded_at` line — check with
`print(chunks[0].metadata)`.

## LlamaIndex

Set the fields on your `Document`s (nodes inherit metadata from their
parent document during parsing):

```python
from datetime import datetime, timezone
from llama_index.core import Document

now = datetime.now(timezone.utc).isoformat()
docs = [Document(text=text,
                 metadata={"source": "docs/refund-policy.md",
                           "embedded_at": now})]
# then your existing flow:
index = VectorStoreIndex.from_documents(docs, storage_context=storage_context)
```

Using `SimpleDirectoryReader`? It sets `file_path` in metadata already —
either map it in raghealth.yaml (`source_path_key: file_path`) or add only
the timestamp. (`file_path` is absolute while your source connector may
record relative paths — raghealth's fuzzy path matching links them
automatically and reports the match rate in every scan header.)

```python
docs = SimpleDirectoryReader("./docs").load_data()
now = datetime.now(timezone.utc).isoformat()
for d in docs:
    d.metadata["embedded_at"] = now
```

## Hand-rolled pipelines

Whatever your insert looks like, include the two keys in the metadata you
already write:

```python
metadata = {
    "source": source_path,                                # you likely have this
    "embedded_at": datetime.now(timezone.utc).isoformat() # add this
}
```

- **pgvector (raw SQL):** put both keys in your JSONB metadata column — or
  add a real `embedded_at TIMESTAMPTZ DEFAULT now()` column, which
  raghealth prefers when present.
- **Chroma:** pass in `metadatas=[...]` on `add`/`upsert`.
- **Qdrant:** put both keys in the point `payload`.

## Verify it worked

```bash
raghealth init      # should show your source and timestamp keys in the
                    # "detected schema" panel — no manual mapping needed
raghealth scan
```

If `init` doesn't detect them, the scan header will still tell you exactly
how many chunks have no source link or no timestamp — that count is your
migration progress meter as re-ingestion brings old chunks under coverage.

## Naming notes

raghealth auto-detects common key names (`source`, `source_path`,
`file_path`, `url`, `page_id`; `embedded_at`, `ingested_at`, `indexed_at`,
`created_at`) — you don't have to use these exact names, but if you're
choosing fresh, `source` and `embedded_at` are the conventions. Timestamps
should be ISO 8601 with timezone (UTC recommended); unix epoch seconds also
work.

---

*Every snippet on this page is verified in CI against real langchain-core,
langchain-chroma, and llama-index-core — including the full round trip:
stamped LangChain documents → Chroma → `raghealth scan` reporting a 100%
link rate with zero unknown timestamps.*
