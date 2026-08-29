"""Abstract interfaces.

Adding support for a new vector DB = implement VectorStoreConnector.
Adding support for a new source system = implement SourceConnector.
Everything else (checks, reports, CLI) works unchanged.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..models import Chunk, SearchHit, SourceDoc


class VectorStoreConnector(ABC):
    """Reads chunks + metadata out of a vector database. Read-only by design."""

    name: str = "unknown-store"

    @abstractmethod
    def fetch_chunks(self, include_embeddings: bool = True,
                     limit: int | None = None) -> Iterable[Chunk]:
        """Yield all chunks in the collection/table.

        include_embeddings=False lets metadata-only checks (staleness, orphans,
        coverage) run without pulling vectors over the wire.
        """
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    supports_search: bool = False

    def search(self, vector: list[float], k: int = 5) -> list[SearchHit]:
        """Similarity search. Optional capability — used by canary queries
        and blast-radius scoring. Connectors that implement it must set
        supports_search = True."""
        raise NotImplementedError(f"{self.name} does not support search")

    def close(self) -> None:  # optional override
        pass


class SourceConnector(ABC):
    """Reads document metadata from the source-of-truth system."""

    name: str = "unknown-source"

    @abstractmethod
    def fetch_documents(self) -> Iterable[SourceDoc]:
        """Yield all documents with their last_modified timestamps."""
        raise NotImplementedError

    def close(self) -> None:  # optional override
        pass
