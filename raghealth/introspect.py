"""Schema introspection for `raghealth init`.

Two layers:
  1. Pure heuristics (detect_* functions) that operate on sampled rows —
     unit-testable without any database.
  2. Thin DB-specific samplers that pull table structure + sample rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

_PATHY = re.compile(r"(/|\\|\.md$|\.txt$|\.pdf$|\.html?$|\.rst$|^https?://)", re.I)
_HEX32 = re.compile(r"^[0-9a-f-]{32,36}$", re.I)


def looks_like_path(v: Any) -> bool:
    return isinstance(v, str) and 1 < len(v) < 2048 and (
        bool(_PATHY.search(v)) or bool(_HEX32.match(v.replace("-", ""))))


def looks_like_timestamp(v: Any) -> bool:
    if isinstance(v, datetime):
        return True
    if isinstance(v, (int, float)):
        return 946_684_800 < v < 4_102_444_800  # 2000..2100 unix seconds
    if isinstance(v, str):
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return False


def _score_key(samples: list[dict], key: str, predicate) -> float:
    vals = [s.get(key) for s in samples if key in s and s.get(key) is not None]
    if not vals:
        return 0.0
    return sum(1 for v in vals if predicate(v)) / len(vals)


def detect_source_key(metadata_samples: list[dict]) -> Optional[str]:
    """Which metadata key holds the link back to the source document?"""
    keys = {k for s in metadata_samples for k in s}
    scored = [(k, _score_key(metadata_samples, k, looks_like_path)) for k in keys]
    scored = [(k, sc) for k, sc in scored if sc >= 0.8]
    if not scored:
        return None
    # prefer conventional names when several keys look pathy
    PREFERRED = ["source_path", "source", "file_path", "path", "url", "document_id", "page_id"]
    scored.sort(key=lambda kv: (PREFERRED.index(kv[0]) if kv[0] in PREFERRED else 99, -kv[1]))
    return scored[0][0]


def detect_timestamp_key(metadata_samples: list[dict]) -> Optional[str]:
    """Which metadata key holds the embedding/ingestion timestamp?"""
    keys = {k for s in metadata_samples for k in s}
    scored = [(k, _score_key(metadata_samples, k, looks_like_timestamp)) for k in keys]
    scored = [(k, sc) for k, sc in scored if sc >= 0.8]
    if not scored:
        return None
    PREFERRED = ["embedded_at", "ingested_at", "indexed_at", "created_at",
                 "updated_at", "timestamp"]
    scored.sort(key=lambda kv: (PREFERRED.index(kv[0]) if kv[0] in PREFERRED else 99, -kv[1]))
    return scored[0][0]


@dataclass
class TableGuess:
    table: str
    schema: str = "public"
    id_col: Optional[str] = None
    content_col: Optional[str] = None
    embedding_col: Optional[str] = None
    metadata_col: Optional[str] = None
    embedded_at_col: Optional[str] = None      # real timestamptz column, if any
    source_key: Optional[str] = None           # metadata key with the source path
    timestamp_key: Optional[str] = None        # metadata key with a timestamp
    row_count: int = 0
    sample_paths: list[str] = field(default_factory=list)

    def to_store_config(self, dsn: str) -> dict:
        c = {"id": self.id_col, "content": self.content_col,
             "embedding": self.embedding_col, "metadata": self.metadata_col}
        if self.source_key and self.metadata_col:
            c["source_path"] = f"{self.metadata_col}->>'{self.source_key}'"
        if self.embedded_at_col:
            c["embedded_at"] = self.embedded_at_col
        elif self.timestamp_key and self.metadata_col:
            c["embedded_at"] = f"{self.metadata_col}->>'{self.timestamp_key}'"
        else:
            c["embedded_at"] = None
        return {"type": "pgvector", "dsn": dsn, "table": self.table,
                "schema": self.schema, "columns": c}


# ---------------------------------------------------------------- pgvector --
def introspect_pgvector(dsn: str, sample_n: int = 50) -> list[TableGuess]:
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True)
    guesses: list[TableGuess] = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.table_schema, c.table_name, c.column_name, c.udt_name,
                       c.data_type
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                WHERE t.table_type = 'BASE TABLE'
                  AND c.table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY c.table_schema, c.table_name, c.ordinal_position""")
            cols_by_table: dict[tuple, list[tuple]] = {}
            for schema, table, col, udt, dtype in cur.fetchall():
                cols_by_table.setdefault((schema, table), []).append((col, udt, dtype))

        for (schema, table), cols in cols_by_table.items():
            vector_cols = [c for c, udt, _ in cols if udt == "vector"]
            if not vector_cols:
                continue
            g = TableGuess(table=table, schema=schema, embedding_col=vector_cols[0])
            text_cols = [c for c, udt, _ in cols if udt in ("text", "varchar")]
            jsonb_cols = [c for c, udt, _ in cols if udt in ("jsonb", "json")]
            ts_cols = [c for c, udt, _ in cols if udt.startswith("timestamp")]
            g.metadata_col = jsonb_cols[0] if jsonb_cols else None
            g.embedded_at_col = ts_cols[0] if ts_cols else None

            with conn.cursor() as cur:
                # primary key -> id column
                cur.execute("""
                    SELECT a.attname FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                     AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = %s::regclass AND i.indisprimary""",
                    (f"{schema}.{table}",))
                pk = [r[0] for r in cur.fetchall()]
                g.id_col = pk[0] if pk else (text_cols[0] if text_cols else None)

                cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                g.row_count = cur.fetchone()[0]

                # sample rows to score text columns and metadata keys
                sample_cols = [c for c in text_cols if c != g.id_col]
                sel = ", ".join(sample_cols + ([g.metadata_col] if g.metadata_col else []))
                if sel:
                    cur.execute(f"SELECT {sel} FROM {schema}.{table} LIMIT {sample_n}")
                    rows = cur.fetchall()
                    # content column = longest average text
                    if sample_cols and rows:
                        avg = {c: 0.0 for c in sample_cols}
                        for r in rows:
                            for i, c in enumerate(sample_cols):
                                avg[c] += len(r[i] or "")
                        g.content_col = max(avg, key=avg.get)
                    if g.metadata_col and rows:
                        metas = []
                        for r in rows:
                            m = r[-1]
                            if isinstance(m, dict):
                                metas.append(m)
                        g.source_key = detect_source_key(metas)
                        g.timestamp_key = detect_timestamp_key(metas)
                        if g.source_key:
                            g.sample_paths = [m[g.source_key] for m in metas
                                              if g.source_key in m][:5]
            guesses.append(g)
    finally:
        conn.close()
    return guesses


# ------------------------------------------------------------------ chroma --
def introspect_chroma(path: Optional[str] = None, host: Optional[str] = None,
                      port: int = 8000, sample_n: int = 50) -> list[dict]:
    """Return per-collection guesses: name, count, source_key, timestamp_key."""
    import chromadb
    client = (chromadb.HttpClient(host=host, port=port) if host
              else chromadb.PersistentClient(path=path or "./chroma_db"))
    out = []
    for col in client.list_collections():
        c = client.get_collection(col.name if hasattr(col, "name") else col)
        res = c.get(limit=sample_n, include=["metadatas"])
        metas = [m or {} for m in (res.get("metadatas") or [])]
        out.append({
            "collection": c.name, "count": c.count(),
            "source_key": detect_source_key(metas),
            "timestamp_key": detect_timestamp_key(metas),
        })
    return out
