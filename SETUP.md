# raghealth — Setup Guide

Everything needed to develop, test, and deploy raghealth, including the exact
test bed used to develop it. Tested on Ubuntu 24.04 / Python 3.10+.

**Windows users:** see the Windows subsections in §1 and §2 for the differences.
Everything else (install, init, scan, playground, server, tests) is pure Python
and works unchanged — backslashes in chunk metadata are normalised automatically.

---

## 1. Install

> **Important:** run all commands from the **repo root** — the directory that
> contains `pyproject.toml`. Do not `cd` into the `raghealth/` package subfolder.

```bash
git clone <your-repo>/raghealth
cd raghealth      # repo root — pyproject.toml is here
```

### Linux / macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[all,server]'        # everything
# minimal: pip install -e .  then add extras as needed:
#   .[pgvector]  .[chroma]  .[qdrant]  .[notion]  .[gdrive]  .[server]
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1            # PowerShell
# or for cmd.exe: .venv\Scripts\activate.bat
pip install -e ".[all,server]"        # double quotes required on Windows
```

Verify in 5 seconds, no database needed:

```bash
raghealth demo --html report.html     # full report incl. blast radius
```

## 2. Test bed (the environment raghealth is developed against)

### Postgres + pgvector

Docker (recommended — one command, works on all platforms):

```bash
docker run -d --name ragpg -e POSTGRES_USER=rag -e POSTGRES_PASSWORD=rag \
  -e POSTGRES_DB=ragdb -p 5432:5432 pgvector/pgvector:pg16
```

**Windows:** use Docker Desktop with the command above. Skip the bare-metal
section below (Linux only).

Bare-metal (Linux — what the dev environment uses):

```bash
sudo apt-get install -y postgresql postgresql-server-dev-16
git clone --depth 1 --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install
sudo service postgresql start
sudo -u postgres psql -c "CREATE ROLE rag LOGIN PASSWORD 'rag';"
sudo -u postgres createdb -O rag ragdb
sudo -u postgres psql -d ragdb -c "CREATE EXTENSION vector;"
```

### Seed the rotten knowledge base

```bash
python scripts/seed_testbed.py --dsn postgresql://rag:rag@localhost:5432/ragdb --docs ./kb-docs
```

> **Windows:** use backslashes for the path: `python scripts\seed_testbed.py ...`
> The script requires `git` on your PATH for change-summary metadata.

This creates a deliberately unhealthy KB with **known expected results**
(see the script's docstring): fresh chunks, stale chunks (with a git-visible
"refund window 14->30 days" change), orphans, a conflicting near-duplicate
pair, and coverage gaps. It uses a deliberately awkward schema — LangChain's
`langchain_pg_embedding` table, timestamps buried inside `cmetadata` jsonb,
absolute chunk paths vs relative source paths — because that's what `init`
and the fuzzy path resolver must handle in the wild.

### Point raghealth at it

```bash
raghealth init --yes --store pgvector \
  --dsn postgresql://rag:rag@localhost:5432/ragdb \
  --source filesystem --source-root ./kb-docs
raghealth scan --html report.html --json scan1.json --fix-queue queue.json
```

Expected: freshness score ~40–60%; staleness finding on `refund-policy.md`
including the change summary; 4 orphaned chunks; 1 cross-source conflict;
2 coverage gaps; link rate ~60% via suffix matching (the deleted docs are
correctly unmatched).

### Canaries (blast radius) on the test bed

The test bed's vectors are synthetic, so use the bundled deterministic
embedder that maps query keywords onto the seeded vector space:

```bash
cp scripts/testbed_embed.py .
cat >> raghealth.yaml << 'EOF'
embedder:
  type: command
  cmd: "python3 testbed_embed.py"
EOF
cp canaries.example.yaml canaries.yaml
raghealth scan --canaries canaries.yaml
raghealth canary baseline && raghealth canary check --fail-under 70
```

For a **real** knowledge base, replace the embedder with the model your
pipeline uses (`type: openai` or `type: sentence_transformers`) — canaries
are only meaningful with your production embedding model.

## 3. Running the tests

```bash
python tests/test_core.py      # 18 tests: matching, introspection, checks,
                               # diff, fix queue, canaries, blast radius
python tests/test_server.py    # 5 tests: auth, ingest, alert rules,
                               # dashboard, payload sanitization
```

Both run with plain python (no pytest needed), though `pytest tests/` works
too. The core suite is DB-free; the server suite uses FastAPI's TestClient
and a throwaway SQLite file. The full end-to-end path (init → scan → diff →
canaries against real Postgres) is exercised by the test bed above.

### Testing coverage — platforms and depth

**Automated CI** (GitHub Actions, `ubuntu-latest`, Python 3.10 / 3.11 / 3.12) — runs on every push:
- Unit + integration tests in `tests/test_core.py` and `tests/test_server.py`
- End-to-end scan against live pgvector (`e2e-pgvector` job in `.github/workflows/ci.yml`)
- GitHub Action self-test against live pgvector (`action-self-test` job)
- LangChain / LlamaIndex snippet execution (`guide-snippets` job)

**Manual smoke testing (v0.5.3):** Windows 11 / Python 3.11 — install from PyPI, `raghealth --version`, `demo`, `demo --html`, `demo --json`, all `--help` subcommands, `init --help`.

**Platforms and paths not yet covered:**
- **macOS** — untested. Community help wanted; if you run raghealth on macOS, please open an issue with anything you hit.
- **Windows in CI** — planned for v0.6 (matrix expansion of the `test` job to include `windows-latest`). A Windows-specific regression test (`test_demo_html_survives_windows_cp1252_env`) is already in the suite and runs on Linux CI to guard against the class of bug, but a true Windows runner is the durable fix.
- **Live Notion / Google Drive** — connectors have unit tests with mocked APIs, no live-service integration test.

**Known Windows quirks (fixed in v0.5.2, hardened in v0.5.3):**
- All raghealth file writes (HTML / JSON / YAML / fix queue) explicitly use UTF-8. Windows default is cp1252, which cannot encode the `⚠` glyph in reports.
- On Windows, `raghealth` reconfigures `sys.stdout` / `sys.stderr` to UTF-8 at CLI start so Rich terminal output survives subprocess / CI / piped contexts.
- `FilesystemSource` may return paths with `\` separators on Windows; if you scan content embedded on one platform from another, normalize to forward slashes in your chunk metadata. The path matcher (`raghealth.matching.normalize`) already handles both — see `test_normalize` for the guarantees.

## 4. Real-world connector setup

### Supabase / pgvector
```yaml
store:
  type: pgvector
  dsn: postgresql://postgres:PASSWORD@db.YOUR-PROJECT.supabase.co:5432/postgres
  table: documents
  # run `raghealth init` instead of writing columns by hand — it introspects
```

### Qdrant
```yaml
store:
  type: qdrant
  url: https://xyz.cloud.qdrant.io:6333
  api_key: env:QDRANT_API_KEY
  collection: my_docs
  source_path_key: source
  embedded_at_key: embedded_at
```

### Chroma
```yaml
store: { type: chroma, path: ./chroma_db, collection: my_docs,
         source_path_key: source, embedded_at_key: embedded_at }
```

### Sources
```yaml
source: { type: filesystem, root: ./docs }          # git-aware
source: { type: notion, token: env:NOTION_TOKEN }   # integration token
source: { type: gdrive, service_account_file: sa.json, folder_id: 1AbC }  # experimental
```

**Metadata contract:** chunks need a `source_path`-like key (any name — init
detects it) and ideally an ingestion timestamp. Missing timestamp? Set
`scan.assume_embedded_at: 2026-06-01` (your last full re-index) for a
degraded-but-useful staleness check.

## 5. Hosted monitoring (agent + server)

Architecture: the **agent** runs next to your data and pushes only findings
metadata (content is force-stripped — see SECURITY.md); the **server** stores
snapshots in SQLite, renders trend dashboards, and sends Slack/email alerts.

### Server (any $5–20 VPS, or Fly.io/Railway)

```bash
pip install 'raghealth[server]'
export RAGHEALTH_DB=/var/lib/raghealth/server.db
export RAGHEALTH_BASE_URL=https://health.yourteam.dev   # used in alert links
python -m raghealth_server create-workspace acme \
  --slack-webhook https://hooks.slack.com/services/T000/B000/XXXX
# prints the API key and the shareable dashboard path /d/<token>
python -m raghealth_server run --port 8080
```

Put nginx/Caddy with TLS in front for production. Optional email alerts:
set `RAGHEALTH_SMTP_HOST/PORT/USER/PASS/FROM/TO`.

Alert rules per workspace (SQLite columns, defaults): score drops ≥ 5 points,
score below 70%, or new findings appear vs the previous snapshot.

### Agent (in your infrastructure)

```yaml
# add to raghealth.yaml
push:
  url: https://health.yourteam.dev
  api_key: env:RAGHEALTH_API_KEY
  kb: production-docs
  canaries: canaries.yaml        # optional: include blast radius
```

```bash
export RAGHEALTH_API_KEY=rh_...
raghealth agent --once             # cron mode
raghealth agent --interval 6h      # daemon mode
```

Cron example (daily at 06:00):
```
0 6 * * * cd /opt/raghealth && RAGHEALTH_API_KEY=rh_... /opt/raghealth/.venv/bin/raghealth agent --once >> agent.log 2>&1
```

systemd (daemon mode): a unit with
`ExecStart=/opt/raghealth/.venv/bin/raghealth agent --interval 6h`,
`Restart=always`, and `EnvironmentFile=/etc/raghealth.env`.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `init` finds no tables | Your vectors aren't pgvector `vector` type, or wrong DB in the DSN |
| Link rate is low | Check `source_path` style vs source connector `path_style`; the scan header shows per-method match counts |
| Everything looks stale after cloning a docs repo | Filesystem source prefers `git log` dates automatically; if there's no git history, mtimes reset on clone — clone with full history |
| Staleness shows "no embedded_at timestamp" | Add an ingestion timestamp to chunk metadata, or set `scan.assume_embedded_at` |
| Canary results look random | You must use the SAME embedding model as your ingestion pipeline |
| Agent: connection refused | Server not running / firewall; test `curl $URL/healthz` |
| Alert fired on first-ever snapshot | Only the below-min-score rule applies without history — that's intended |
