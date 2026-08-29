# Security & Data Handling

raghealth is designed to be pointed at production knowledge bases, so its
data-handling posture is deliberately conservative:

**Nothing leaves your machine.** raghealth is a local CLI. It makes no network
calls except to the database and source systems *you* configure. There is no
telemetry, no analytics, no phone-home.

**Read-only by construction.** The pgvector connector opens its session with
`set_session(readonly=True)` — the database itself will reject any write.
All other connectors only ever call read APIs.

**Credentials stay yours.** Connection strings live in your local
`raghealth.yaml` (add it to `.gitignore`) or environment variables
(`token: env:NOTION_TOKEN`). raghealth never stores or transmits them.

**Content redaction.** Run `raghealth scan --redact` to strip all chunk text
from reports. Findings then reference only IDs, paths, timestamps, and
similarity scores — safe to share outside the team that owns the data.

**Minimal content by default.** Even without `--redact`, the pgvector
connector fetches at most a 500-character preview per chunk
(`content_preview_chars`), never full documents.

**Reporting a vulnerability:** please open a private security advisory on the
GitHub repository rather than a public issue.
