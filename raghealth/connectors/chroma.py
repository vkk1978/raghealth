"""Chroma connector (local persistent dir or HTTP server).

Config example:

    store:
      type: chroma
      path: ./chroma_db            # or host/port for client-server mode
      collection: my_docs
      source_path_key: source      # which metadata key links back to the doc
      embedded_at_key: embedded_at # optional metadata key with ISO timestamp
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import Chunk, SearchHit
from .base import VectorStoreConnector


def _parse_ts(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):  # unix ts
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class ChromaConnector(VectorStoreConnector):
    name = "chroma"
    supports_search = True

    def __init__(self, collection: str,
                 path: Optional[str] = None,
                 host: Optional[str] = None, port: int = 8000,
                 source_path_key: str = "source",
                 embedded_at_key: str = "embedded_at",
                 batch_size: int = 500):
        try:
            import chromadb
        except ImportError as e:
            raise ImportError(
                "chroma connector requires chromadb: "
                "pip install 'raghealth[chroma]' or pip install chromadb"
            ) from e

        if host:
            client = chromadb.HttpClient(host=host, port=port)
        else:
            client = chromadb.PersistentClient(path=path or "./chroma_db")
        self._col = client.get_collection(collection)
        self.source_key = source_path_key
        self.embedded_at_key = embedded_at_key
        self.batch = batch_size

    def count(self) -> int:
        return self._col.count()

    def fetch_chunks(self, include_embeddings: bool = True,
                     limit: int | None = None) -> Iterable[Chunk]:
        include = ["documents", "metadatas"]
        if include_embeddings:
            include.append("embeddings")
        total = self.count() if limit is None else min(limit, self.count())
        offset = 0
        while offset < total:
            n = min(self.batch, total - offset)
            res = self._col.get(limit=n, offset=offset, include=include)
            ids = res["ids"]
            docs = res.get("documents") or [None] * len(ids)
            metas = res.get("metadatas") or [{}] * len(ids)
            embs = res.get("embeddings")
            for i, _id in enumerate(ids):
                meta = metas[i] or {}
                emb = None
                if include_embeddings and embs is not None:
                    e = embs[i]
                    emb = list(map(float, e)) if e is not None else None
                yield Chunk(
                    id=str(_id),
                    content=docs[i],
                    embedding=emb,
                    embedded_at=_parse_ts(meta.get(self.embedded_at_key)),
                    source_path=meta.get(self.source_key),
                    metadata=meta,
                )
            offset += n

    def search(self, vector, k: int = 5):
        res = self._col.query(query_embeddings=[vector], n_results=k,
                              include=["documents", "metadatas", "distances"])
        hits = []
        ids = res["ids"][0]
        docs = (res.get("documents") or [[None] * len(ids)])[0]
        metas = (res.get("metadatas") or [[{}] * len(ids)])[0]
        dists = (res.get("distances") or [[0.0] * len(ids)])[0]
        for i, _id in enumerate(ids):
            meta = metas[i] or {}
            hits.append(SearchHit(chunk_id=str(_id), score=1.0 - float(dists[i]),
                                  source_path=meta.get(self.source_key),
                                  content=docs[i]))
        return hits
