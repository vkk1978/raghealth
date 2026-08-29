# raghealth

**Health checks for RAG knowledge bases.** Your vector index decays silently: source docs get edited, deleted, and duplicated while the embeddings stay frozen — and semantic similarity gives no warning, because a stale chunk scores just as high as a fresh one. `raghealth` compares your vector store against its source of truth and tells you exactly what's rotten.

No pipeline instrumentation. No code changes. **Read-only — enforced at the connection level** (the pgvector session is opened read-only; every connector uses read APIs only). Nothing leaves your machine.

| | Supported today | Planned |
|---|---|---|
| **Vector stores** | pgvector / Supabase (incl. LangChain's default table) · Chroma · Qdrant | Pinecone · Weaviate — [request/vote](https://github.com/vkk1978/raghealth/issues) |
| **Sources** | filesystem / git · Notion · Google Drive (experimental) | Confluence · Zendesk |
| **Pipelines** | any — needs 2 metadata keys; see the [3-line pipeline guide](https://github.com/vkk1978/raghealth/blob/main/guides/pipeline-metadata.md) | |

Install: `pip install raghealth` — or `pipx install raghealth` / `uvx raghealth` if you don't want to touch a virtualenv.

```
╭──────────── raghealth — knowledge base health ────────────╮
│ 45.5% of chunks are fresh and linked to a live source     │
╰───────────────────────────────────────────────────────────╯
STALENESS   12 of 27 linked chunks (44%) are stale
  critical  8 stale chunk(s) from 'Refund Policy' — source
            updated 5 days ago, chunks embedded 40 days before
ORPHANS     6 chunks point at sources that no longer exist
DUPLICATES  'vacation: 15 days' ≈ 'vacation: 20 days' from two
            different source docs — conflicting retrieval
COVERAGE    2 live documents were never ingested
```

## The four checks

| Check | Question it answers | Failure mode it catches |
|---|---|---|
| **Staleness** | Was the source edited after the chunk was embedded? | Assistant confidently serves last quarter's policy |
| **Orphans** | Does the chunk's source still exist? | Deleted/archived docs still being retrieved and cited |
| **Duplicates** | Are near-identical chunks coming from different sources? | Two versions of the same fact → contradictory answers |
| **Coverage** | Which live documents were never ingested? | "I don't know" for knowledge that exists |

All checks use metadata and the vectors **already stored in your DB** — zero embedding-API cost.

## Quick start

```bash
pip install raghealth            # core
pip install 'raghealth[all]'     # + pgvector, chroma, notion connectors

raghealth demo --html report.html    # see it work in 5 seconds, no DB needed

raghealth init                   # 60-second setup: connects to your DB,
                                 # auto-detects the schema, writes raghealth.yaml
raghealth scan --html report.html --json report.json
raghealth scan --redact          # metadata-only report, no chunk text
```

`raghealth init` introspects your database: it finds tables with vector
columns, samples your metadata to detect which key holds the source path and
which holds a timestamp, and shows you the guessed mapping before writing
anything. Works with non-standard schemas (LangChain's
`langchain_pg_embedding`, custom tables, timestamps buried in jsonb).

Use `--fail-on-critical` in CI to block deploys when the knowledge base is rotten.

## GitHub Action: gate deploys on knowledge-base health

Run the health check in CI — on docs PRs, on a weekly schedule, or before a
deploy — with a job-summary report and typed outputs:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }      # full history for reliable timestamps
- uses: vkk1978/raghealth@v0
  env:
    RAGHEALTH_PG_DSN: ${{ secrets.RAGHEALTH_PG_DSN }}
  with:
    config: raghealth.yaml
    fail-on-critical: "true"
    # baseline: baseline.json   # fail on regression vs a known-good scan
```

Outputs `freshness-score`, `critical-findings`, and paths to the HTML
report and fix queue. Full example: [`examples/kb-health.yml`](https://github.com/vkk1978/raghealth/blob/main/examples/kb-health.yml).
The action's own CI runs it against a live pgvector service on every push.

## From report to fix

A diagnosis you can't act on gets run once. raghealth ends every scan with
something a pipeline can consume:

```bash
raghealth scan --fix-queue queue.json   # actionable job: reembed / delete / ingest
raghealth diff last-week.json today.json --fail-on-regression
```

The **fix queue** lists exactly which chunks to re-embed (stale), delete
(orphaned), ingest (missing), or review (conflicting versions), sorted by
priority — feed it to your LangChain/LlamaIndex ingestion script.

**`raghealth diff`** compares two scans: new findings, resolved findings, and
metric deltas. Run it weekly, or in CI with `--fail-on-regression`.

**What actually changed:** for git-backed sources, stale findings include a
summary of the edits since embedding — so you can tell a typo fix from a
policy change without leaving the report:

```
critical  5 stale chunk(s) from 'refund-policy'
          ... What changed: 2 commits since embedding; latest:
          'refund window 14->30 days'.
```

## Supported today

- **Vector stores:** pgvector / Supabase, Chroma, Qdrant (local, server, or cloud)
- **Sources:** filesystem / git repo (reliable timestamps + change summaries from `git log`), Notion, Google Drive (experimental)

Adding a connector = implementing one small interface (`connectors/base.py`). PRs welcome — Qdrant, Pinecone, Weaviate, Google Drive, and Confluence are next.

## Requirements for full accuracy

Your chunks need two pieces of metadata (most pipelines already store the first):

1. `source_path` — an identifier linking the chunk back to its source document
2. `embedded_at` — when the embedding was created

Missing `embedded_at`? Set `scan.assume_embedded_at` (e.g. your last full re-index date) for a degraded-but-useful staleness check. Missing `source_path`? raghealth will tell you, loudly — that's finding #1.

## Python API

```python
from raghealth import scan
from raghealth.connectors.pgvector import PgVectorConnector
from raghealth.sources.filesystem import FilesystemSource
from raghealth.report import render_html

store = PgVectorConnector(dsn="postgresql://...", table="documents")
source = FilesystemSource(root="./docs")
report = scan(store, source)

print(report.freshness_score)          # 45.5
open("report.html", "w").write(render_html(report))
```

## Hosted monitoring: agent + dashboard

One-off scans find rot; monitoring catches it the day it happens. raghealth
ships a self-hostable monitoring layer with a strict privacy split:

- **Agent** (`raghealth agent --once` in cron, or `--interval 6h`) runs in
  *your* infrastructure next to your DB and docs. It pushes only findings
  metadata — scores, titles, source paths, severities. Content is
  force-stripped and chunk IDs are reduced to counts; your credentials and
  vectors never leave your machine.
- **Server** (`pip install 'raghealth[server]'`, SQLite, runs on a $5 VPS)
  stores snapshot history, renders a shareable read-only trend dashboard at
  `/d/<token>`, and sends Slack/email alerts when the score drops, falls
  below threshold, or new findings appear vs the previous snapshot — with
  blast-radius context right in the alert.

```bash
# server
python -m raghealth_server create-workspace acme --slack-webhook https://hooks.slack.com/...
python -m raghealth_server run --port 8080
# agent (after adding push: section to raghealth.yaml)
raghealth agent --once
```

See [SETUP.md](https://github.com/vkk1978/raghealth/blob/main/SETUP.md) for
full deployment instructions (cron, systemd, TLS, SMTP) — or just
`docker compose up -d` from [`deploy/`](https://github.com/vkk1978/raghealth/tree/main/deploy),
which includes one-command recipes for Fly.io and Render.

## Retrieval impact: canaries and blast radius

A stale chunk nobody retrieves is housekeeping. A stale chunk at rank 1 for a
question your users ask daily is actively poisoning answers. v0.4 tells them
apart.

Define your canonical questions once (`canaries.yaml`), point raghealth at the
same embedding model your pipeline uses (`embedder:` in raghealth.yaml —
OpenAI, local sentence-transformers, or any shell command), then:

```bash
raghealth scan --canaries canaries.yaml     # blast-radius scoring
raghealth canary baseline                   # snapshot top-k per question
raghealth canary check --fail-under 70      # weekly / CI drift check
```

With `--canaries`, stale and orphaned findings get escalated and annotated
when the flagged chunks actually appear in canary results:

```
critical  5 stale chunk(s) from 'refund-policy'
          ... ⚠ ACTIVE: 4 of these chunks are retrieved by canary
          'refund-window' at rank 1.
```

`canary check` measures top-k overlap against the baseline — healthy systems
hold 85-95%; a drop below 70% means retrieval has drifted and needs a look.

## Fuzzy source linking

Chunk metadata rarely matches source paths exactly — absolute vs relative
paths, `file://` URLs, Windows separators, Notion IDs with or without dashes.
raghealth links them automatically (exact → normalized → path-suffix →
basename → Notion-ID strategies) and reports the match rate and method
breakdown in every scan, so linking is auditable, never silent:

```
source linking: 94.0% of 212 distinct chunk paths matched (exact:180, suffix:19, basename:1)
```

## Security

Local-only, read-only, zero telemetry, `--redact` for content-free reports.
See [SECURITY.md](https://github.com/vkk1978/raghealth/blob/main/SECURITY.md).

## Roadmap

- [x] Re-embed queue export + `raghealth diff` — shipped in 0.3
- [x] Qdrant connector, Google Drive source — shipped in 0.3
- [ ] Pinecone, Weaviate connectors; Confluence source
- [x] Canary queries & blast-radius scoring — shipped in 0.4
- [x] Agent + self-hostable server: scheduled scans, trend dashboards, Slack/email alerts — shipped in 0.5
- [ ] Managed cloud version of the server

Guides: [pipeline metadata (3 lines)](https://github.com/vkk1978/raghealth/blob/main/guides/pipeline-metadata.md) · [orchestration](https://github.com/vkk1978/raghealth/blob/main/guides/orchestration.md) · [for product owners](https://github.com/vkk1978/raghealth/blob/main/guides/for-product-owners.md) · [QMS wording](https://github.com/vkk1978/raghealth/blob/main/guides/qms-wording.md)

Setup, test bed, and deployment: [SETUP.md](https://github.com/vkk1978/raghealth/blob/main/SETUP.md) · Security model: [SECURITY.md](https://github.com/vkk1978/raghealth/blob/main/SECURITY.md)

MIT licensed.
