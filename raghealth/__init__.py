"""raghealth — health checks for RAG knowledge bases.

Detects stale chunks, orphaned chunks, duplicate/conflicting content, and
coverage gaps by comparing your vector store against its source of truth.
"""
__version__ = "0.5.4"

from .scanner import scan  # noqa: F401
from .models import Chunk, SourceDoc, HealthReport  # noqa: F401
