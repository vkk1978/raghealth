#!/usr/bin/env python3
"""Seed the raghealth test bed: a deliberately rotten knowledge base.

Creates in Postgres (LangChain-style schema, timestamps inside jsonb — the
messy real-world case) plus a git-backed docs directory:

  - 4 FRESH chunks   (onboarding — embedded after last doc edit)
  - 5 STALE chunks   (refund policy — doc edited after embedding)
  - 3 ORPHANS        (api-v1 doc deleted from the repo)
  - 1 ORPHAN         (2024 vacation policy — archived, near-duplicate of current)
  - 1 CONFLICT pair  (vacation 15 days vs 20 days, cosine ~0.999)
  - 2 COVERAGE gaps  (terms.md, roadmap.md exist but never ingested)

Expected scan result: score ~40-60%, every check fires.

Usage:
    python scripts/seed_testbed.py \
        --dsn postgresql://rag:rag@localhost:5432/ragdb \
        --docs ./kb-docs
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(7)
NOW = datetime.now(timezone.utc)
DIM = 32


def vec(seed: int, jitter: float = 0.0) -> str:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    if jitter:
        v = [x + random.gauss(0, jitter) for x in v]
    n = math.sqrt(sum(x * x for x in v)) or 1
    return "[" + ",".join(f"{x/n:.6f}" for x in v) + "]"


def seed_postgres(dsn: str) -> None:
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute(f"""
        DROP TABLE IF EXISTS langchain_pg_embedding;
        CREATE TABLE langchain_pg_embedding (
          uuid TEXT PRIMARY KEY,
          document TEXT,
          embedding vector({DIM}),
          cmetadata JSONB,
          collection_id TEXT);""")
    rows = []

    def add(id_, doc, seed, days_old, source, jitter=0.0):
        rows.append((id_, doc, vec(seed, jitter), json.dumps({
            "source": source,
            "ingested_at": (NOW - timedelta(days=days_old)).isoformat()}),
            "kb-main"))

    for i in range(4):   # FRESH (doc committed 20d ago, embedded 3d ago)
        add(f"a{i}", f"Onboarding step {i}", 100 + i, 3,
            "/data/docs/handbook/onboarding.md")
    for i in range(5):   # STALE (doc committed 2d ago, embedded 45d ago)
        add(f"b{i}", f"Refunds within 14 days, part {i}", 200 + i, 45,
            "/data/docs/policies/refund-policy.md")
    for i in range(3):   # ORPHANS (doc never exists in repo)
        add(f"c{i}", f"API v1 endpoint {i}", 300 + i, 120,
            "/data/docs/engineering/api-v1.md")
    # CONFLICT pair (same seed => cosine ~0.999) + one side is an orphan
    add("d0", "Vacation: 15 days per year", 400, 200,
        "/data/docs/hr/vacation-2024.md")
    add("d1", "Vacation: 20 days per year", 400, 4,
        "/data/docs/hr/vacation.md", jitter=0.02)

    cur.executemany(
        "INSERT INTO langchain_pg_embedding VALUES (%s,%s,%s::vector,%s::jsonb,%s)",
        rows)
    conn.commit()
    conn.close()
    print(f"postgres: seeded {len(rows)} chunks into langchain_pg_embedding")


def git(docs: Path, *args, date: datetime | None = None) -> None:
    env = dict(os.environ)
    if date:
        stamp = date.strftime("%Y-%m-%dT%H:%M:%S")
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", "-C", str(docs), "-c", "user.email=seed@raghealth",
                    "-c", "user.name=seed", *args], check=True, env=env,
                   capture_output=True)


def seed_docs(docs: Path) -> None:
    if docs.exists():
        raise SystemExit(f"{docs} already exists — remove it first")
    for d in ("handbook", "policies", "hr", "legal", "products"):
        (docs / d).mkdir(parents=True)
    git(docs, "init", "-q", ".")
    # initial commit, 20 days ago
    (docs / "handbook/onboarding.md").write_text("# Onboarding\n")
    (docs / "policies/refund-policy.md").write_text("# Refund policy: 14 days\n")
    (docs / "hr/vacation.md").write_text("# Vacation: 20 days\n")
    (docs / "legal/terms.md").write_text("# ToS — never ingested\n")
    (docs / "products/roadmap.md").write_text("# Roadmap — never ingested\n")
    git(docs, "add", ".")
    git(docs, "commit", "-qm", "initial docs", date=NOW - timedelta(days=20))
    # refund policy updated 2 days ago — AFTER its chunks were embedded (45d)
    (docs / "policies/refund-policy.md").write_text(
        "# Refund policy — UPDATED to 30 days\n")
    git(docs, "add", ".")
    git(docs, "commit", "-qm", "refund window 14->30 days",
        date=NOW - timedelta(days=2))
    print(f"docs: git repo at {docs} (2 backdated commits)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dsn", default="postgresql://rag:rag@localhost:5432/ragdb")
    p.add_argument("--docs", default="./kb-docs")
    p.add_argument("--skip-postgres", action="store_true")
    p.add_argument("--skip-docs", action="store_true")
    args = p.parse_args()
    if not args.skip_postgres:
        seed_postgres(args.dsn)
    if not args.skip_docs:
        seed_docs(Path(args.docs))
    print("\nNext:")
    print(f"  raghealth init --yes --store pgvector --dsn '{args.dsn}' \\")
    print(f"      --source filesystem --source-root {args.docs}")
    print("  raghealth scan --html report.html")
    print("Expected: score ~40-60%, all four checks firing.")


if __name__ == "__main__":
    main()
