# raghealth

raghealth is a health-check tool for the vector index behind a retrieval-augmented generation (RAG) application. It compares your vector store against the source documents those vectors were built from, and it reports the ways in which the two have diverged since the vectors were created.

You do not need to add instrumentation to your ingestion pipeline or make changes to your application code in order to use raghealth. The tool reads from an existing vector store and an existing set of source documents, produces a report, and hands you a queue of specific chunks to re-embed, delete, ingest, or review.

## Why raghealth exists

A RAG index does not fail loudly when it goes out of sync with the documents it was built from. Similarity scores continue to look healthy even when the source document behind a chunk has been edited since the chunk was embedded, has been deleted, or now has multiple versions in the index with conflicting content. Cosine similarity measures semantic similarity between a query and a chunk. It has no signal for whether the chunk still reflects the current source, so teams often discover the drift only when an end user notices a wrong or missing answer.

raghealth closes this gap. It validates the index against its sources directly, using metadata and vectors that are already stored in your database, so a health check does not require any additional embedding calls or external API cost.

## The four checks

raghealth reports four categories of divergence between the vector store and its sources.

| Check | Question the check answers | Failure mode the check catches |
|---|---|---|
| Staleness | Was the source document edited after the chunk was embedded? | The assistant returns last quarter's policy because the current one has not been re-embedded. |
| Orphans | Does the chunk's source document still exist? | Deleted or archived documents continue to be retrieved and cited. |
| Duplicates | Are near-identical chunks arriving from different sources? | Two versions of the same fact produce contradictory answers. |
| Coverage | Which live documents were never ingested? | The assistant answers "I don't know" for knowledge that is available. |

Every check operates on data that is already inside your vector store. The four checks do not create new embeddings during a scan, so a check-only scan incurs no cost on your embedding provider. The optional canary-query feature described later in this document does require an embedder in order to embed the canary questions themselves.

## What raghealth supports today

The table below lists the vector stores, source connectors, and ingestion pipelines that raghealth supports in the current release.

| Category | Supported today | Planned |
|---|---|---|
| Vector stores | pgvector and Supabase (including LangChain's default `langchain_pg_embedding` table), Chroma, and Qdrant. | Pinecone and Weaviate. You can request or upvote a connector on the [GitHub issue tracker](https://github.com/vkk1978/raghealth/issues). |
| Source connectors | Filesystem, git repository, Notion, and Google Drive (experimental). | Confluence and Zendesk. |
| Ingestion pipelines | Any pipeline that stores two metadata keys per chunk. See the [pipeline metadata guide](https://github.com/vkk1978/raghealth/blob/main/guides/pipeline-metadata.md) for the three-line change most pipelines need. | — |

## Access model and privacy

raghealth is designed to be safe to point at a production vector store.

The tool is read-only, and this is enforced at the connection level rather than at the application level. raghealth opens the pgvector session in read-only mode, and every vector-store and source connector calls only read APIs. The tool cannot modify your vector store or your source documents even if it is instructed to.

raghealth runs entirely inside your own infrastructure. The tool does not send your vector data, your source documents, or your database credentials to any third-party service. When you run a scan with the `--redact` flag, the resulting report contains metadata and counts only, and it omits the text of every chunk.

## Installation

Install the core package from PyPI.

```bash
pip install raghealth
```

If you would rather not create a virtual environment, you can install raghealth as an isolated tool.

```bash
pipx install raghealth
# or
uvx raghealth
```

To install raghealth together with the pgvector, Chroma, and Notion connectors, use the `all` extra.

```bash
pip install 'raghealth[all]'
```

## Quick start

The commands in this section walk you from a first demonstration to a real scan of your own database.

### Run the built-in demonstration

The demonstration command uses a bundled synthetic knowledge base and does not require a database connection. It is the fastest way to see the shape of a raghealth report.

```bash
raghealth demo --html report.html
```

### Point raghealth at your own vector store

The `init` command inspects your database and writes a configuration file. raghealth searches for tables that contain a vector column, samples the metadata to determine which key holds the source path and which key holds the embedding timestamp, and displays the mapping it inferred before it writes anything to disk. The command supports non-standard schemas, including LangChain's `langchain_pg_embedding` table and custom tables that store their timestamps inside a `jsonb` column.

```bash
raghealth init
```

Once `raghealth.yaml` exists, you can run a full scan.

```bash
raghealth scan --html report.html --json report.json
```

To produce a report that contains no chunk text, add the `--redact` flag.

```bash
raghealth scan --redact
```

### Example report output

A scan prints a short summary to the terminal and writes the detailed report to the paths you specified.

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

## Using raghealth in CI

You can run raghealth as a GitHub Action so that a knowledge-base health check runs on every pull request to your documentation repository, on a weekly schedule, or before a deploy.

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }      # full history is required for reliable timestamps
- uses: vkk1978/raghealth@v0
  env:
    RAGHEALTH_PG_DSN: ${{ secrets.RAGHEALTH_PG_DSN }}
  with:
    config: raghealth.yaml
    fail-on-critical: "true"
    # baseline: baseline.json   # fail the job on regression against a known-good scan
```

The action produces the outputs `freshness-score`, `critical-findings`, and file paths for the HTML report and the fix queue. A complete example is available at [`examples/kb-health.yml`](https://github.com/vkk1978/raghealth/blob/main/examples/kb-health.yml). The action's own continuous-integration workflow runs it against a live pgvector service on every commit.

To block a deploy when the knowledge base is in poor health, add the `--fail-on-critical` flag to your `raghealth scan` invocation. The command exits with a non-zero status code when the scan produces any finding at the `critical` severity.

## From a report to a fix

Every scan ends with two artefacts that a pipeline can consume: a fix queue and a diff.

### The fix queue

The fix queue lists every action that raghealth recommends. Each entry names a specific chunk and specifies whether the chunk should be re-embedded (staleness), deleted (orphan), ingested (coverage gap), or reviewed (duplicate conflict). The entries are sorted by priority, and the queue can be fed directly to your LangChain or LlamaIndex ingestion script.

```bash
raghealth scan --fix-queue queue.json
```

### The diff

The `diff` command compares two scans and reports three things: findings that are new since the earlier scan, findings that have been resolved since the earlier scan, and the change in each metric. You can run `diff` on a weekly schedule, or you can run it in CI with `--fail-on-regression` so that a deploy is blocked when the knowledge base has become less healthy.

```bash
raghealth diff last-week.json today.json --fail-on-regression
```

### What actually changed

For sources that are backed by a git repository, a staleness finding includes a summary of the commits that touched the source document after the chunk was embedded. This allows you to tell a typo fix apart from a policy change without leaving the report.

```
critical  5 stale chunk(s) from 'refund-policy'
          ... What changed: 2 commits since embedding; latest:
          'refund window 14->30 days'.
```

## Requirements for full accuracy

raghealth needs two pieces of metadata on each chunk. Most ingestion pipelines already store the first key, and the second key is straightforward to add.

1. `source_path` — an identifier that links the chunk back to its source document.
2. `embedded_at` — the timestamp at which the embedding was created.

If your pipeline does not record `embedded_at`, you can set `scan.assume_embedded_at` in `raghealth.yaml` to a fixed date, such as the date of your last full re-index. This produces a degraded but still useful staleness check. If your pipeline does not record `source_path`, raghealth reports the missing key as the first finding in the scan.

## Python API

You can call raghealth as a library rather than as a command-line tool.

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

## Scheduled monitoring

A one-off scan finds issues that are already present in the index. A scheduled scan detects issues on the day they appear. raghealth ships a self-hostable monitoring layer that consists of two components, and the two components are separated so that no source content ever leaves your infrastructure.

### The agent

The agent runs inside your own infrastructure, adjacent to your database and your source documents. You can run the agent as a one-shot command from cron, or you can run it as a long-lived process with a polling interval.

```bash
raghealth agent --once
raghealth agent --interval 6h
```

The agent pushes only findings metadata to the server. The metadata consists of scores, document titles, source paths, and severity levels. The agent strips all chunk text before it transmits a report, and it replaces chunk identifiers with counts. Your database credentials and your vector data never leave the machine on which the agent runs.

### The server

The server accepts findings metadata from one or more agents, stores snapshot history in SQLite, renders a shareable read-only trend dashboard at `/d/<token>`, and sends Slack and email alerts when the freshness score drops, when the score falls below a configured threshold, or when new findings appear compared with the previous snapshot. The server is designed to run on a small virtual machine, and a low-tier instance is sufficient for typical workloads.

```bash
pip install 'raghealth[server]'
python -m raghealth_server create-workspace acme \
    --slack-webhook https://hooks.slack.com/...
python -m raghealth_server run --port 8080
```

To connect the agent to the server, add a `push:` section to `raghealth.yaml` and run the agent as described above.

For complete deployment instructions, including cron, systemd, TLS, and SMTP configuration, see [SETUP.md](https://github.com/vkk1978/raghealth/blob/main/SETUP.md). If you would rather deploy the server with Docker, run `docker compose up -d` from the [`deploy/`](https://github.com/vkk1978/raghealth/tree/main/deploy) directory, which also contains one-command recipes for Fly.io and Render.

## Retrieval impact: canary queries and blast radius

Not every stale chunk affects your users equally. A stale chunk that no query ever retrieves has a smaller impact than a stale chunk that appears at rank 1 for a question your users ask every day. The canary-query feature distinguishes the two.

To use canary queries, define a list of canonical questions in `canaries.yaml` and configure raghealth to use the same embedding model that your ingestion pipeline uses. The embedder can be an OpenAI model, a local sentence-transformer model, or any shell command that produces an embedding.

```bash
raghealth scan --canaries canaries.yaml     # produces blast-radius scoring
raghealth canary baseline                   # records the top-k results for each canary
raghealth canary check --fail-under 70      # checks drift against the baseline
```

When you scan with `--canaries`, raghealth annotates staleness and orphan findings that involve chunks which are actually retrieved by one or more canary queries. The report escalates these findings and identifies the canary query and the retrieval rank.

```
critical  5 stale chunk(s) from 'refund-policy'
          ... ACTIVE: 4 of these chunks are retrieved by canary
          'refund-window' at rank 1.
```

The `canary check` command measures the top-k overlap between the current retrieval results and the baseline. A healthy system typically retains 85–95 percent of the baseline results. A drop below 70 percent indicates that retrieval has drifted, and the report highlights the queries that have changed the most.

## Source linking

Chunk metadata rarely matches source paths exactly. Different pipelines write absolute paths, relative paths, `file://` URLs, Windows-style separators, or Notion identifiers that may or may not include dashes. raghealth links a chunk to its source using a sequence of matching strategies: exact match, normalised match, path-suffix match, basename match, and Notion-identifier match.

Every scan reports the overall match rate and the breakdown of matches by strategy, so that the linking step is auditable.

```
source linking: 94.0% of 212 distinct chunk paths matched (exact:180, suffix:19, basename:1)
```

## Security

raghealth is a local-only, read-only tool. The tool does not send telemetry, and it does not open outbound network connections other than the connections you configure it to make (for example, the connection to your vector database, or the connection to your push server). The `--redact` flag produces a report that contains counts and metadata only, and it excludes every chunk of source text. For the complete security model and the threat analysis, see [SECURITY.md](https://github.com/vkk1978/raghealth/blob/main/SECURITY.md).

## Roadmap

The roadmap below is grouped by release. Items marked with `[x]` have shipped in the release named after them.

- Version 0.3
  - [x] Fix-queue export and the `raghealth diff` command
  - [x] Qdrant connector and Google Drive source connector
- Version 0.4
  - [x] Canary queries and blast-radius scoring
- Version 0.5
  - [x] Agent and self-hostable server, including scheduled scans, trend dashboards, and Slack and email alerts
- Planned
  - [ ] Pinecone and Weaviate connectors
  - [ ] Confluence source connector
  - [ ] Managed cloud version of the server

## Further reading

The following guides cover topics that do not fit in this README.

- [Pipeline metadata guide](https://github.com/vkk1978/raghealth/blob/main/guides/pipeline-metadata.md) explains the three-line change that most ingestion pipelines need in order to record `source_path` and `embedded_at`.
- [Orchestration guide](https://github.com/vkk1978/raghealth/blob/main/guides/orchestration.md) describes how to run raghealth alongside Airflow, Prefect, or Dagster.
- [Guide for product owners](https://github.com/vkk1978/raghealth/blob/main/guides/for-product-owners.md) explains the raghealth metrics in non-technical language.
- [QMS wording guide](https://github.com/vkk1978/raghealth/blob/main/guides/qms-wording.md) provides suggested wording for quality-management-system documentation.

For setup, test-bed, and deployment procedures, see [SETUP.md](https://github.com/vkk1978/raghealth/blob/main/SETUP.md). For the security model, see [SECURITY.md](https://github.com/vkk1978/raghealth/blob/main/SECURITY.md).

## Licence

raghealth is released under the MIT licence.
