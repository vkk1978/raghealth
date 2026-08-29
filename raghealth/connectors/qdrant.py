"""Qdrant connector (local path, or remote URL/host with optional API key).

Config example:

    store:
      type: qdrant
      url: https://xyz.cloud.qdrant.io:6333   # or path: ./qdrant_data (local)
      api_key: env:QDRANT_API_KEY             # optional
      collection: my_docs
      source_path_key: source                 # payload key linking to the doc
      embedded_at_key: embedded_at            # payload key with ISO ts / epoch
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import Chunk, SearchHit
from .base import VectorStoreConnector


def _parse_ts(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _resolve_secret(v: Optional[str]) -> Optional[str]:
    if v and v.startswith("env:"):
        return os.environ.get(v[4:])
    return v


class QdrantConnector(VectorStoreConnector):
    name = "qdrant"
    supports_search = True

    def __init__(self, collection: str,
                 url: Optional[str] = None,
                 path: Optional[str] = None,
                 host: Optional[str] = None, port: int = 6333,
                 api_key: Optional[str] = None,
                 source_path_key: str = "source",
                 embedded_at_key: str = "embedded_at",
                 content_key: str = "text",
                 batch_size: int = 512):
        try:
            from qdrant_client import QdrantClient
        except ImportError as e:
            raise ImportError(
                "qdrant connector requires qdrant-client: "
                "pip install 'raghealth[qdrant]' or pip install qdrant-client") from e
        if url:
            self._client = QdrantClient(url=url, api_key=_resolve_secret(api_key))
        elif host:
            self._client = QdrantClient(host=host, port=port,
                                        api_key=_resolve_secret(api_key))
        else:
            self._client = QdrantClient(path=path or "./qdrant_data")
        self.collection = collection
        self.source_key = source_path_key
        self.embedded_at_key = embedded_at_key
        self.content_key = content_key
        self.batch = batch_size

    def count(self) -> int:
        return self._client.count(self.collection, exact=True).count

    @staticmethod
    def _vector_of(point) -> Optional[list[float]]:
        v = point.vector
        if v is None:
            return None
        if isinstance(v, dict):  # named vectors — take the first
            v = next(iter(v.values()), None)
            if v is None:
                return None
        return [float(x) for x in v]

    def fetch_chunks(self, include_embeddings: bool = True,
                     limit: int | None = None) -> Iterable[Chunk]:
        fetched = 0
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection,
                limit=self.batch,
                offset=offset,
                with_payload=True,
                with_vectors=include_embeddings,
            )
            for p in points:
                payload = p.payload or {}
                yield Chunk(
                    id=str(p.id),
                    content=payload.get(self.content_key),
                    embedding=self._vector_of(p) if include_embeddings else None,
                    embedded_at=_parse_ts(payload.get(self.embedded_at_key)),
                    source_path=payload.get(self.source_key),
                    metadata=payload,
                )
                fetched += 1
                if limit and fetched >= limit:
                    return
            if offset is None:
                return

    def search(self, vector, k: int = 5):
        res = self._client.query_points(self.collection, query=vector,
                                        limit=k, with_payload=True)
        hits = []
        for p in res.points:
            payload = p.payload or {}
            hits.append(SearchHit(chunk_id=str(p.id), score=float(p.score),
                                  source_path=payload.get(self.source_key),
                                  content=payload.get(self.content_key)))
        return hits

    def close(self) -> None:
        self._client.close()
