"""Fuzzy source-path matching.

Real pipelines store source references inconsistently: absolute vs relative
paths, file:// or https:// URLs, Windows separators, Notion IDs with or
without dashes. This module links chunk.source_path values to SourceDoc.path
values across those styles, and reports HOW each link was made so users can
audit the matching instead of trusting it blindly.

Strategies, in order of confidence:
  exact       — string equality
  normalized  — equal after scheme/slash/case normalization
  suffix      — one normalized path ends with the other (path-segment aligned);
                longest unique overlap wins
  basename    — same final filename, only if unambiguous
  notion_id   — 32-hex Notion IDs compared dash-insensitively
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.I)


def normalize(path: str) -> str:
    p = path.strip()
    if _SCHEME.match(p):                      # strip scheme and host
        p = _SCHEME.sub("", p)
        p = p.split("/", 1)[1] if "/" in p else p
    p = p.replace("\\", "/")
    p = re.sub(r"/{2,}", "/", p)
    while p.startswith("./"):
        p = p[2:]
    p = p.strip("/")
    p = p.split("?", 1)[0].split("#", 1)[0]   # drop query/fragment
    return p.casefold()


def notion_id(path: str) -> Optional[str]:
    """Extract a 32-hex Notion ID from a raw ID, dashed UUID, or notion URL."""
    tail = normalize(path).rsplit("/", 1)[-1]
    tail = tail.rsplit("-", 1)[-1] if "-" in tail and len(tail) > 36 else tail
    candidate = tail.replace("-", "")
    return candidate if _HEX32.match(candidate) else None


@dataclass
class LinkStats:
    total: int = 0
    matched: int = 0
    by_method: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ambiguous: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return round(100.0 * self.matched / self.total, 1) if self.total else 100.0


class PathResolver:
    def __init__(self, source_paths: list[str]):
        self.sources = list(dict.fromkeys(source_paths))  # dedupe, keep order
        self._exact = set(self.sources)
        self._norm: dict[str, list[str]] = defaultdict(list)
        self._base: dict[str, list[str]] = defaultdict(list)
        self._nid: dict[str, list[str]] = defaultdict(list)
        for s in self.sources:
            n = normalize(s)
            self._norm[n].append(s)
            self._base[n.rsplit("/", 1)[-1]].append(s)
            nid = notion_id(s)
            if nid:
                self._nid[nid].append(s)
        self.stats = LinkStats()
        self._cache: dict[str, tuple[Optional[str], str]] = {}

    # -- strategies -----------------------------------------------------------
    def _suffix_match(self, n: str) -> tuple[Optional[str], bool]:
        """Longest path-segment-aligned suffix overlap. Returns (match, ambiguous)."""
        best: list[str] = []
        best_len = 0
        for norm_s, originals in self._norm.items():
            longer, shorter = (n, norm_s) if len(n) >= len(norm_s) else (norm_s, n)
            if longer == shorter or longer.endswith("/" + shorter):
                if len(shorter) > best_len:
                    best, best_len = list(originals), len(shorter)
                elif len(shorter) == best_len:
                    best.extend(originals)
        if len(best) == 1:
            return best[0], False
        return None, len(best) > 1

    def resolve(self, chunk_path: str) -> tuple[Optional[str], str]:
        """Return (canonical source path or None, method)."""
        if chunk_path in self._cache:
            return self._cache[chunk_path]
        result = self._resolve(chunk_path)
        self._cache[chunk_path] = result

        self.stats.total += 1
        matched, method = result
        if matched:
            self.stats.matched += 1
            self.stats.by_method[method] += 1
        elif method == "ambiguous":
            self.stats.ambiguous.append(chunk_path)
        else:
            self.stats.unmatched.append(chunk_path)
        return result

    def _resolve(self, chunk_path: str) -> tuple[Optional[str], str]:
        if chunk_path in self._exact:
            return chunk_path, "exact"
        n = normalize(chunk_path)
        if n in self._norm and len(self._norm[n]) == 1:
            return self._norm[n][0], "normalized"
        m, ambiguous = self._suffix_match(n)
        if m:
            return m, "suffix"
        if ambiguous:
            return None, "ambiguous"
        base = n.rsplit("/", 1)[-1]
        if base in self._base:
            if len(self._base[base]) == 1:
                return self._base[base][0], "basename"
            return None, "ambiguous"
        nid = notion_id(chunk_path)
        if nid and nid in self._nid and len(self._nid[nid]) == 1:
            return self._nid[nid][0], "notion_id"
        return None, "unmatched"
