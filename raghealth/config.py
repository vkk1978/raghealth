"""Config loading (raghealth.yaml) and connector construction."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .connectors.base import SourceConnector, VectorStoreConnector


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for key in ("store", "source"):
        if key not in cfg:
            raise ValueError(f"config missing required section: '{key}'")
    return cfg


def build_store(cfg: dict[str, Any]) -> VectorStoreConnector:
    kind = cfg.pop("type", None)
    if kind == "pgvector":
        from .connectors.pgvector import PgVectorConnector
        return PgVectorConnector(**cfg)
    if kind == "chroma":
        from .connectors.chroma import ChromaConnector
        return ChromaConnector(**cfg)
    if kind == "qdrant":
        from .connectors.qdrant import QdrantConnector
        return QdrantConnector(**cfg)
    raise ValueError(f"unknown store type: {kind!r} (supported: pgvector, chroma, qdrant)")


def build_source(cfg: dict[str, Any]) -> SourceConnector:
    kind = cfg.pop("type", None)
    if kind == "filesystem":
        from .sources.filesystem import FilesystemSource
        return FilesystemSource(**cfg)
    if kind == "notion":
        from .sources.notion import NotionSource
        return NotionSource(**cfg)
    if kind == "gdrive":
        from .sources.gdrive import GDriveSource
        return GDriveSource(**cfg)
    raise ValueError(f"unknown source type: {kind!r} (supported: filesystem, notion, gdrive)")


def parse_scan_options(cfg: dict[str, Any]) -> dict[str, Any]:
    opts = dict(cfg.get("scan") or {})
    out: dict[str, Any] = {}
    out["grace_days"] = float(opts.get("grace_days", 7))
    out["duplicate_threshold"] = float(opts.get("duplicate_threshold", 0.97))
    out["include_embeddings"] = bool(opts.get("include_embeddings", True))
    if opts.get("assume_embedded_at"):
        dt = datetime.fromisoformat(str(opts["assume_embedded_at"]))
        out["assume_embedded_at"] = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if opts.get("limit"):
        out["limit"] = int(opts["limit"])
    return out
