"""pgvector / Supabase connector.

Works against any Postgres table that stores embeddings via the pgvector
extension. Column names are configurable because every team's schema differs.

Config example (raghealth.yaml):

    store:
      type: pgvector
      dsn: postgresql://user:pass@db.xxx.supabase.co:5432/postgres
      table: documents
      columns:
        id: id
        content: content
        embedding: embedding
        embedded_at: embedded_at          # or null if you don't have one
        source_path: metadata->>'source'  # supports jsonb path expressions
        metadata: metadata

If `embedded_at` is missing from the schema, staleness falls back to
comparing against the scan's `assume_embedded_at` config (e.g. the date of
your last full re-index) — degraded but still useful.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import Chunk, SearchHit
from .base import VectorStoreConnector

DEFAULT_COLUMNS = {
    "id": "id",
    "content": "content",
    "embedding": "embedding",
    "embedded_at": "embedded_at",
    "source_path": "metadata->>'source_path'",
    "metadata": "metadata",
}


def _parse_embedding(raw) -> Optional[list[float]]:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    if isinstance(raw, str):  # pgvector returns '[0.1,0.2,...]'
        raw = raw.strip()
        if raw.startswith("["):
            return [float(x) for x in raw[1:-1].split(",") if x]
    return None


def _parse_ts(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class PgVectorConnector(VectorStoreConnector):
    name = "pgvector"
    supports_search = True

    def __init__(self, dsn: str, table: str,
                 columns: Optional[dict] = None,
                 schema: str = "public",
                 content_preview_chars: int = 500):
        try:
            import psycopg2  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "pgvector connector requires psycopg2: "
                "pip install 'raghealth[pgvector]' or pip install psycopg2-binary"
            ) from e
        import psycopg2

        self.table = table
        self.schema = schema
        self.cols = {**DEFAULT_COLUMNS, **(columns or {})}
        self.preview = content_preview_chars
        self._conn = psycopg2.connect(dsn)
        self._conn.set_session(readonly=True)  # safety: never write

    # -- helpers ------------------------------------------------------------
    def _select_clause(self, include_embeddings: bool) -> str:
        c = self.cols
        emb = f"{c['embedding']}::text" if include_embeddings else "NULL"
        embedded_at = c.get("embedded_at") or "NULL"
        return (
            f"SELECT {c['id']}::text AS id, "
            f"LEFT({c['content']}, {self.preview}) AS content, "
            f"{emb} AS embedding, "
            f"{embedded_at} AS embedded_at, "
            f"{c['source_path']} AS source_path, "
            f"{c['metadata']}::text AS metadata "
            f"FROM {self.schema}.{self.table}"
        )

    # -- interface ----------------------------------------------------------
    def count(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self.schema}.{self.table}")
            return cur.fetchone()[0]

    def fetch_chunks(self, include_embeddings: bool = True,
                     limit: int | None = None) -> Iterable[Chunk]:
        sql = self._select_clause(include_embeddings)
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn.cursor(name="raghealth_scan") as cur:  # server-side cursor
            cur.itersize = 1000
            cur.execute(sql)
            for row in cur:
                _id, content, emb, embedded_at, source_path, meta = row
                try:
                    metadata = json.loads(meta) if meta else {}
                except (TypeError, ValueError):
                    metadata = {}
                yield Chunk(
                    id=_id,
                    content=content,
                    embedding=_parse_embedding(emb) if include_embeddings else None,
                    embedded_at=_parse_ts(embedded_at),
                    source_path=source_path,
                    metadata=metadata,
                )

    def search(self, vector, k: int = 5):
        c = self.cols
        qvec = "[" + ",".join(f"{float(x):.8f}" for x in vector) + "]"
        sql = (f"SELECT {c['id']}::text, "
               f"1 - ({c['embedding']} <=> %s::vector) AS score, "
               f"{c['source_path']}, LEFT({c['content']}, 200) "
               f"FROM {self.schema}.{self.table} "
               f"ORDER BY {c['embedding']} <=> %s::vector LIMIT %s")
        with self._conn.cursor() as cur:
            cur.execute(sql, (qvec, qvec, k))
            return [SearchHit(chunk_id=r[0], score=float(r[1]),
                              source_path=r[2], content=r[3])
                    for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
