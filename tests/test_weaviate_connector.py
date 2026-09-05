"""Tests for the Weaviate connector.

Two test tiers:

1. Unit tests — always run. Cover pure-function helpers (property auto-detection,
   secret resolution, timestamp parsing, named-vector selection) without a
   Weaviate server.

2. End-to-end test — runs against a live Weaviate on http://localhost:8080
   (override via WEAVIATE_TEST_URL env var). Seeds a small collection with
   dummy chunks, then exercises count / fetch_chunks / search against the
   real server. Skipped automatically if no Weaviate is reachable.

To run the e2e locally:

    docker compose -f tests/docker-compose.weaviate.yml up -d
    pytest tests/test_weaviate_connector.py -v
    docker compose -f tests/docker-compose.weaviate.yml down
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

from raghealth.connectors.weaviate import (
    WeaviateConnector,
    _first_present,
    _parse_ts,
    _pick_vector,
    _resolve_secret,
    _TEXT_PROPERTY_CANDIDATES,
    _SOURCE_PROPERTY_CANDIDATES,
    _EMBEDDED_AT_PROPERTY_CANDIDATES,
)


# ============================================================ unit tests ====

def test_parse_ts_none():
    assert _parse_ts(None) is None


def test_parse_ts_iso_string_with_z():
    dt = _parse_ts("2026-06-01T12:00:00Z")
    assert dt == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_ts_iso_string_with_offset():
    dt = _parse_ts("2026-06-01T12:00:00+00:00")
    assert dt == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_ts_naive_string_promoted_to_utc():
    dt = _parse_ts("2026-06-01T12:00:00")
    assert dt is not None and dt.tzinfo == timezone.utc


def test_parse_ts_unix_epoch():
    dt = _parse_ts(1_750_000_000)
    assert dt is not None and dt.tzinfo == timezone.utc


def test_parse_ts_datetime_naive_promoted():
    dt = _parse_ts(datetime(2026, 6, 1, 12, 0))
    assert dt.tzinfo == timezone.utc


def test_parse_ts_datetime_aware_preserved():
    original = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert _parse_ts(original) == original


def test_parse_ts_garbage_returns_none():
    assert _parse_ts("not a date") is None
    assert _parse_ts(object()) is None


def test_resolve_secret_env_prefix(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sekret")
    assert _resolve_secret("env:MY_KEY") == "sekret"


def test_resolve_secret_env_missing(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    assert _resolve_secret("env:NOPE") is None


def test_resolve_secret_literal_passes_through():
    assert _resolve_secret("literal-value") == "literal-value"


def test_resolve_secret_none():
    assert _resolve_secret(None) is None


def test_first_present_finds_first_match():
    props = {"content": "hi", "text": "hello", "chunk": 1}
    assert _first_present(_TEXT_PROPERTY_CANDIDATES, props) == "text"


def test_first_present_none_match():
    assert _first_present(_TEXT_PROPERTY_CANDIDATES, {"random": 1}) is None


def test_pick_vector_none():
    assert _pick_vector(None, None) is None


def test_pick_vector_empty_dict():
    assert _pick_vector({}, None) is None


def test_pick_vector_single_unnamed():
    """v4 returns single-vector collections as {'default': [...]} — auto-selects."""
    assert _pick_vector({"default": [1.0, 2.0, 3.0]}, None) == [1.0, 2.0, 3.0]


def test_pick_vector_multiple_requires_name():
    with pytest.raises(ValueError, match="multiple named vectors"):
        _pick_vector({"a": [1.0], "b": [2.0]}, None)


def test_pick_vector_multiple_with_name_selects_correctly():
    result = _pick_vector({"a": [1.0], "b": [2.0]}, "b")
    assert result == [2.0]


def test_pick_vector_name_not_found_raises():
    with pytest.raises(ValueError, match="not found"):
        _pick_vector({"a": [1.0]}, "b")


def test_pick_vector_bare_list_returned_as_is():
    """Defensive path for client versions that return a bare list."""
    result = _pick_vector([1.0, 2.0], None)
    assert result == [1.0, 2.0]


def test_property_candidate_lists_ordered_by_convention():
    """Text falls back sensibly."""
    assert _TEXT_PROPERTY_CANDIDATES[0] == "text"
    assert _SOURCE_PROPERTY_CANDIDATES[0] == "source"
    assert _EMBEDDED_AT_PROPERTY_CANDIDATES[0] == "embedded_at"


def test_chunk_from_object_populates_all_fields():
    """WeaviateConnector._chunk_from_object with a MagicMock stand-in for weaviate object."""
    # Build a connector with a mocked client to avoid touching weaviate
    conn = _build_stubbed_connector()

    fake_obj = MagicMock()
    fake_obj.uuid = "abc-123"
    fake_obj.properties = {
        "text": "Hello world",
        "source": "docs/hello.md",
        "embedded_at": "2026-06-01T12:00:00Z",
        "extra": "keep me",
    }
    fake_obj.vector = {"default": [0.1, 0.2, 0.3]}

    chunk = conn._chunk_from_object(fake_obj, include_embeddings=True)

    assert chunk.id == "abc-123"
    assert chunk.content == "Hello world"
    assert chunk.source_path == "docs/hello.md"
    assert chunk.embedded_at == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert chunk.embedding == [0.1, 0.2, 0.3]
    assert chunk.metadata["extra"] == "keep me"
    assert "_tenant" not in chunk.metadata


def test_chunk_from_object_tenant_added_to_metadata():
    conn = _build_stubbed_connector()

    fake_obj = MagicMock()
    fake_obj.uuid = "abc-123"
    fake_obj.properties = {"text": "hi", "source": "s"}
    fake_obj.vector = None

    chunk = conn._chunk_from_object(fake_obj, include_embeddings=False, tenant="tenant_a")

    assert chunk.metadata["_tenant"] == "tenant_a"


def test_chunk_from_object_include_embeddings_false():
    conn = _build_stubbed_connector()

    fake_obj = MagicMock()
    fake_obj.uuid = "abc"
    fake_obj.properties = {"text": "hi"}
    fake_obj.vector = {"default": [1.0]}

    chunk = conn._chunk_from_object(fake_obj, include_embeddings=False)
    assert chunk.embedding is None


def _build_stubbed_connector() -> WeaviateConnector:
    """Return a WeaviateConnector that bypasses __init__ (no weaviate connection needed)."""
    conn = WeaviateConnector.__new__(WeaviateConnector)
    conn._client = MagicMock()
    conn._collection = MagicMock()
    conn._collection_name = "TestCol"
    conn._vector_name = None
    conn._tenants = []
    conn._text_property = "text"
    conn._source_property = "source"
    conn._embedded_at_property = "embedded_at"
    conn._batch = 100
    conn._timeout = 30
    conn._properties_detected = True
    return conn


# =============================================================== e2e ====

_TEST_URL = os.environ.get("WEAVIATE_TEST_URL", "http://localhost:8080")
_TEST_COLLECTION = "RaghealthWeaviateConnectorTest"


def _weaviate_available() -> bool:
    """Return True if a Weaviate REST endpoint is reachable at _TEST_URL."""
    parsed = urlparse(_TEST_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (OSError, ValueError):
        return False


e2e = pytest.mark.skipif(
    not _weaviate_available(),
    reason=(
        f"Weaviate not reachable at {_TEST_URL}. "
        "Start it with: docker compose -f tests/docker-compose.weaviate.yml up -d"
    ),
)


@pytest.fixture(scope="module")
def seeded_weaviate() -> Iterator[dict]:
    """Seed a collection with dummy chunks and return metadata for tests.

    Dummy data: 5 objects, each with text / source / embedded_at properties and
    a 4-dim vector. Two share the same content to enable duplicate testing later.
    """
    import weaviate
    from weaviate.classes.config import Configure, Property, DataType

    parsed = urlparse(_TEST_URL)
    http_host = parsed.hostname or "localhost"
    http_port = parsed.port or 8080
    http_secure = parsed.scheme == "https"

    client = weaviate.connect_to_custom(
        http_host=http_host,
        http_port=http_port,
        http_secure=http_secure,
        grpc_host=http_host,
        grpc_port=50051,
        grpc_secure=http_secure,
    )

    # Clean slate
    if client.collections.exists(_TEST_COLLECTION):
        client.collections.delete(_TEST_COLLECTION)

    client.collections.create(
        name=_TEST_COLLECTION,
        vectorizer_config=Configure.Vectorizer.none(),
        properties=[
            Property(name="text", data_type=DataType.TEXT),
            Property(name="source", data_type=DataType.TEXT),
            Property(name="embedded_at", data_type=DataType.DATE),
        ],
    )

    collection = client.collections.get(_TEST_COLLECTION)

    # Deterministic dummy vectors so search assertions are predictable
    dummy_objects = [
        {
            "properties": {
                "text": "The waiter served our main course.",
                "source": "docs/restaurant.md",
                "embedded_at": "2026-06-01T12:00:00Z",
            },
            "vector": [1.0, 0.0, 0.0, 0.0],
        },
        {
            "properties": {
                "text": "The player hit an ace down the line.",
                "source": "docs/tennis.md",
                "embedded_at": "2026-06-02T12:00:00Z",
            },
            "vector": [0.0, 1.0, 0.0, 0.0],
        },
        {
            "properties": {
                "text": "The municipal corporation serves residents.",
                "source": "docs/government.md",
                "embedded_at": "2026-06-03T12:00:00Z",
            },
            "vector": [0.0, 0.0, 1.0, 0.0],
        },
        {
            "properties": {
                "text": "The tax department served a notice.",
                "source": "docs/legal.md",
                "embedded_at": "2026-06-04T12:00:00Z",
            },
            "vector": [0.0, 0.0, 0.0, 1.0],
        },
        {
            "properties": {
                "text": "The waiter served our main course.",   # duplicate content
                "source": "docs/restaurant_v2.md",
                "embedded_at": "2026-06-05T12:00:00Z",
            },
            "vector": [0.99, 0.0, 0.0, 0.1],                     # near-duplicate vector
        },
    ]

    with collection.batch.dynamic() as batch:
        for obj in dummy_objects:
            batch.add_object(properties=obj["properties"], vector=obj["vector"])

    client.close()

    yield {
        "url": _TEST_URL,
        "collection": _TEST_COLLECTION,
        "expected_count": len(dummy_objects),
        "expected_sources": {o["properties"]["source"] for o in dummy_objects},
    }

    # Teardown
    client = weaviate.connect_to_custom(
        http_host=http_host,
        http_port=http_port,
        http_secure=http_secure,
        grpc_host=http_host,
        grpc_port=50051,
        grpc_secure=http_secure,
    )
    if client.collections.exists(_TEST_COLLECTION):
        client.collections.delete(_TEST_COLLECTION)
    client.close()


@e2e
def test_e2e_count_matches_seed(seeded_weaviate):
    conn = WeaviateConnector(
        collection=seeded_weaviate["collection"],
        url=seeded_weaviate["url"],
    )
    try:
        assert conn.count() == seeded_weaviate["expected_count"]
    finally:
        conn.close()


@e2e
def test_e2e_fetch_chunks_metadata_only(seeded_weaviate):
    conn = WeaviateConnector(
        collection=seeded_weaviate["collection"],
        url=seeded_weaviate["url"],
    )
    try:
        chunks = list(conn.fetch_chunks(include_embeddings=False))
        assert len(chunks) == seeded_weaviate["expected_count"]

        sources = {c.source_path for c in chunks}
        assert sources == seeded_weaviate["expected_sources"]

        # All chunks should have parsed timestamps
        for c in chunks:
            assert c.embedded_at is not None
            assert c.embedded_at.tzinfo is not None
            assert c.content is not None
            assert c.embedding is None  # embeddings excluded
    finally:
        conn.close()


@e2e
def test_e2e_fetch_chunks_with_embeddings(seeded_weaviate):
    conn = WeaviateConnector(
        collection=seeded_weaviate["collection"],
        url=seeded_weaviate["url"],
    )
    try:
        chunks = list(conn.fetch_chunks(include_embeddings=True))
        assert len(chunks) == seeded_weaviate["expected_count"]

        for c in chunks:
            assert c.embedding is not None
            assert len(c.embedding) == 4
            assert all(isinstance(x, float) for x in c.embedding)
    finally:
        conn.close()


@e2e
def test_e2e_fetch_chunks_respects_limit(seeded_weaviate):
    conn = WeaviateConnector(
        collection=seeded_weaviate["collection"],
        url=seeded_weaviate["url"],
    )
    try:
        chunks = list(conn.fetch_chunks(include_embeddings=False, limit=2))
        assert len(chunks) == 2
    finally:
        conn.close()


@e2e
def test_e2e_search_finds_nearest(seeded_weaviate):
    """Query with a vector close to the tennis object; expect it as top hit."""
    conn = WeaviateConnector(
        collection=seeded_weaviate["collection"],
        url=seeded_weaviate["url"],
    )
    try:
        # Tennis object has vector [0,1,0,0]; query with same direction
        hits = conn.search([0.0, 1.0, 0.0, 0.0], k=3)
        assert len(hits) == 3
        assert hits[0].source_path == "docs/tennis.md"
        # Cosine similarity of parallel vectors is 1.0; distance 0; score should be 1.0
        assert hits[0].score == pytest.approx(1.0, abs=0.01)
    finally:
        conn.close()


@e2e
def test_e2e_connection_error_actionable_message():
    """Bad host produces a ConnectionError with an actionable hint."""
    with pytest.raises(ConnectionError, match="is Weaviate running"):
        WeaviateConnector(
            collection="whatever",
            url="http://localhost:1",   # port 1: reserved, guaranteed unreachable
        )


@e2e
def test_e2e_missing_collection_lists_available(seeded_weaviate):
    """Requesting a nonexistent collection surfaces the available ones."""
    with pytest.raises(ValueError, match="not found"):
        WeaviateConnector(
            collection="DefinitelyNotACollection",
            url=seeded_weaviate["url"],
        )
