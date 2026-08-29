# Contributing to raghealth

The highest-impact contribution is a **connector**. Each one is a single
small class:

- **Vector store** → implement `VectorStoreConnector` in
  `raghealth/connectors/base.py` (`fetch_chunks`, `count`, optionally
  `search` for canary support). Register it in `raghealth/config.py`.
  Wanted: Pinecone, Weaviate, Milvus, Elasticsearch, Turbopuffer.
- **Source** → implement `SourceConnector` (`fetch_documents`; optionally
  `describe_change` for change summaries). Wanted: Confluence, SharePoint,
  GitHub wiki, Slab, GitBook.

Look at `connectors/qdrant.py` (60 lines) or `sources/notion.py` for the
pattern. Keep dependencies optional (a new extra in `pyproject.toml`),
resolve secrets via the `env:VAR` convention, and stay strictly read-only.

## Ground rules

- **Read-only, always.** No connector may write to a user's store or source.
- **Content stays local.** Nothing may add document content to agent push
  payloads (`raghealth/agent.py`); excerpts belong in `excerpt_*` data keys,
  which the serializer drops. `tests/test_server.py::test_payload_sanitization`
  enforces this.
- Tests: `python tests/test_core.py && python tests/test_server.py`.
  For pgvector changes, run the e2e: `python scripts/seed_testbed.py ...`
  then `raghealth scan` (expected results are in the script docstring).
- One feature per PR, and update SETUP.md if config surface changes.

## Stability policy

Stable surfaces (breaking changes only with a major version + changelog
notice): CLI commands and flags, the scan/diff/fix-queue JSON schemas, the
agent push payload, and the `VectorStoreConnector`/`SourceConnector` ABCs.
Everything else (internal modules, report HTML markup) may change between
minor versions. Tests are plain-python files that also run under
`pytest tests/`.

## Reporting bugs

Real-world schema weirdness is exactly what we want to hear about — if
`raghealth init` guessed your schema wrong or the path resolver missed your
linking style, that's a bug. Please include the (redacted) `detected schema`
panel and your chunk metadata shape.
