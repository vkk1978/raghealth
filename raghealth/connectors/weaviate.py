"""Weaviate connector (local, custom endpoint, or Weaviate Cloud Services).

Requires weaviate-client v4 (v3 is a different, incompatible package).

Config example:

    store:
      type: weaviate
      # exactly one of the connection modes:
      url: http://localhost:8080              # custom endpoint
      # local: true                           # equivalent, connect_to_local()
      # wcs_cluster_url: https://xyz.weaviate.network   # Weaviate Cloud

      api_key: env:WEAVIATE_API_KEY           # optional
      collection: DocChunks                   # required
      vector_name: default                    # required if collection has multiple named vectors
      tenants:                                # optional; iterate each tenant if set
        - tenant_a

      text_property: text                     # default: auto-detect
      source_path_property: source            # default: auto-detect
      embedded_at_property: embedded_at       # default: auto-detect

      batch_size: 100
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..models import Chunk, SearchHit
from .base import VectorStoreConnector

# Auto-detection fallback lists — checked in order against the first object's properties.
_TEXT_PROPERTY_CANDIDATES = ("text", "content", "chunk", "body")
_SOURCE_PROPERTY_CANDIDATES = ("source", "source_path", "url", "path")
_EMBEDDED_AT_PROPERTY_CANDIDATES = ("embedded_at", "created_at", "ingested_at", "indexed_at")


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _resolve_secret(value: Optional[str]) -> Optional[str]:
    if value and value.startswith("env:"):
        return os.environ.get(value[4:])
    return value


def _first_present(candidates: tuple[str, ...], properties: dict[str, Any]) -> Optional[str]:
    """Return the first candidate key that exists in properties, else None."""
    for key in candidates:
        if key in properties:
            return key
    return None


def _pick_vector(vector: Any, vector_name: Optional[str]) -> Optional[list[float]]:
    """Extract a single vector from Weaviate's obj.vector (dict of named vectors).

    weaviate-client v4 always returns obj.vector as a dict, even for collections
    with a single unnamed vector (key "default"). If vector_name is set, use it.
    If not set and only one vector exists, use it. If not set and multiple exist,
    raise — silently picking a vector produces meaningless canary scores.
    """
    if vector is None:
        return None
    if not isinstance(vector, dict):
        # Defensive: some client versions may return a bare list
        return [float(x) for x in vector]
    if not vector:
        return None
    if vector_name is not None:
        if vector_name not in vector:
            raise ValueError(
                f"weaviate: vector_name '{vector_name}' not found in object "
                f"(available: {sorted(vector.keys())})"
            )
        return [float(x) for x in vector[vector_name]]
    if len(vector) == 1:
        return [float(x) for x in next(iter(vector.values()))]
    raise ValueError(
        f"weaviate: collection has multiple named vectors {sorted(vector.keys())}; "
        f"set 'vector_name' in config to select one"
    )


class WeaviateConnector(VectorStoreConnector):
    """Read chunks and run similarity search against a Weaviate collection.

    Supports single-tenant and multi-tenant collections, and both single-vector
    and named-vector schemas. Read-only.
    """

    name = "weaviate"
    supports_search = True

    def __init__(
        self,
        collection: str,
        url: Optional[str] = None,
        local: bool = False,
        wcs_cluster_url: Optional[str] = None,
        api_key: Optional[str] = None,
        vector_name: Optional[str] = None,
        tenants: Optional[list[str]] = None,
        text_property: Optional[str] = None,
        source_path_property: Optional[str] = None,
        embedded_at_property: Optional[str] = None,
        batch_size: int = 100,
        timeout_seconds: int = 30,
    ):
        try:
            import weaviate
            from weaviate.classes.init import Auth
        except ImportError as e:
            raise ImportError(
                "weaviate connector requires weaviate-client v4: "
                "pip install 'raghealth[weaviate]' or pip install 'weaviate-client>=4.7,<5'"
            ) from e

        # v3 vs v4 detection — v3 does not expose classes.init.Auth
        wv_version = getattr(weaviate, "__version__", "unknown")
        if not wv_version.startswith("4."):
            raise ImportError(
                f"weaviate connector requires weaviate-client v4.x, found {wv_version}. "
                "Upgrade with: pip install --upgrade 'weaviate-client>=4.7,<5'"
            )

        resolved_key = _resolve_secret(api_key)
        auth_credentials = Auth.api_key(resolved_key) if resolved_key else None

        # Connection mode selection (exactly one)
        modes_set = sum(1 for m in (url, local, wcs_cluster_url) if m)
        if modes_set > 1:
            raise ValueError(
                "weaviate: set exactly one of 'url', 'local', or 'wcs_cluster_url'"
            )

        try:
            if wcs_cluster_url:
                self._client = weaviate.connect_to_wcs(
                    cluster_url=wcs_cluster_url,
                    auth_credentials=auth_credentials,
                )
            elif url:
                # Parse host/port from URL
                from urllib.parse import urlparse
                parsed = urlparse(url)
                http_host = parsed.hostname or "localhost"
                http_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                http_secure = parsed.scheme == "https"
                self._client = weaviate.connect_to_custom(
                    http_host=http_host,
                    http_port=http_port,
                    http_secure=http_secure,
                    grpc_host=http_host,
                    grpc_port=50051,
                    grpc_secure=http_secure,
                    auth_credentials=auth_credentials,
                )
            else:
                # local mode — connect_to_local() defaults to http://localhost:8080
                self._client = weaviate.connect_to_local(auth_credentials=auth_credentials)
        except Exception as e:
            target = wcs_cluster_url or url or "http://localhost:8080"
            raise ConnectionError(
                f"weaviate: could not connect to {target} — "
                f"is Weaviate running? ({type(e).__name__}: {e})"
            ) from e

        # Validate collection exists
        if not self._client.collections.exists(collection):
            available = [c for c in self._client.collections.list_all(simple=True).keys()]
            self._client.close()
            raise ValueError(
                f"weaviate: collection '{collection}' not found. "
                f"Available: {available}"
            )

        self._collection_name = collection
        self._collection = self._client.collections.get(collection)
        self._vector_name = vector_name
        self._tenants = tenants or []
        self._text_property = text_property
        self._source_property = source_path_property
        self._embedded_at_property = embedded_at_property
        self._batch = batch_size
        self._timeout = timeout_seconds

        # Property auto-detection deferred to first fetch (needs a real object)
        self._properties_detected = False

    # -------------------------------------------------------------- helpers ----
    def _detect_properties(self, sample_properties: dict[str, Any]) -> None:
        """Fill in text/source/embedded_at property names from a sample object."""
        if self._text_property is None:
            self._text_property = _first_present(_TEXT_PROPERTY_CANDIDATES, sample_properties)
        if self._source_property is None:
            self._source_property = _first_present(_SOURCE_PROPERTY_CANDIDATES, sample_properties)
        if self._embedded_at_property is None:
            self._embedded_at_property = _first_present(
                _EMBEDDED_AT_PROPERTY_CANDIDATES, sample_properties
            )
        self._properties_detected = True

    def _collection_for_tenant(self, tenant: Optional[str]):
        if tenant:
            return self._collection.with_tenant(tenant)
        return self._collection

    def _chunk_from_object(self, obj: Any, include_embeddings: bool,
                           tenant: Optional[str] = None) -> Chunk:
        properties = obj.properties or {}
        if not self._properties_detected:
            self._detect_properties(properties)

        embedding = None
        if include_embeddings:
            embedding = _pick_vector(obj.vector, self._vector_name)

        metadata: dict[str, Any] = dict(properties)
        if tenant:
            metadata["_tenant"] = tenant

        return Chunk(
            id=str(obj.uuid),
            content=properties.get(self._text_property) if self._text_property else None,
            embedding=embedding,
            embedded_at=_parse_ts(
                properties.get(self._embedded_at_property) if self._embedded_at_property else None
            ),
            source_path=properties.get(self._source_property) if self._source_property else None,
            metadata=metadata,
        )

    # -------------------------------------------------- VectorStoreConnector ----
    def count(self) -> int:
        if self._tenants:
            total = 0
            for t in self._tenants:
                total += self._collection_for_tenant(t).aggregate.over_all(total_count=True).total_count
            return total
        return self._collection.aggregate.over_all(total_count=True).total_count

    def fetch_chunks(self, include_embeddings: bool = True,
                     limit: int | None = None) -> Iterable[Chunk]:
        tenants: list[Optional[str]] = list(self._tenants) if self._tenants else [None]
        fetched = 0
        for tenant in tenants:
            col = self._collection_for_tenant(tenant)
            iterator_kwargs: dict[str, Any] = {}
            if include_embeddings:
                # v4: include_vector=True yields all vectors; can also be a list of named vectors
                iterator_kwargs["include_vector"] = True
            for obj in col.iterator(**iterator_kwargs):
                yield self._chunk_from_object(obj, include_embeddings, tenant)
                fetched += 1
                if limit is not None and fetched >= limit:
                    return

    def search(self, vector: list[float], k: int = 5) -> list[SearchHit]:
        from weaviate.classes.query import MetadataQuery

        # For multi-tenant, search only the first tenant (canaries are per-tenant conceptually)
        col = self._collection_for_tenant(self._tenants[0] if self._tenants else None)

        near_kwargs: dict[str, Any] = {
            "near_vector": vector,
            "limit": k,
            "return_metadata": MetadataQuery(score=True, distance=True),
        }
        if self._vector_name:
            near_kwargs["target_vector"] = self._vector_name

        response = col.query.near_vector(**near_kwargs)

        hits: list[SearchHit] = []
        for obj in response.objects:
            properties = obj.properties or {}
            if not self._properties_detected:
                self._detect_properties(properties)
            # v4 near_vector: metadata.distance is cosine distance; score = 1 - distance
            distance = obj.metadata.distance if obj.metadata.distance is not None else None
            score = 1.0 - float(distance) if distance is not None else 0.0
            hits.append(SearchHit(
                chunk_id=str(obj.uuid),
                score=score,
                source_path=properties.get(self._source_property) if self._source_property else None,
                content=properties.get(self._text_property) if self._text_property else None,
            ))
        return hits

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
