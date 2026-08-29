"""Filesystem source connector.

Treats a directory tree (docs repo, exported wiki, markdown knowledge base)
as the source of truth. If the directory is a git repo, last_modified comes
from `git log` (much more reliable than filesystem mtime, which resets on
clone/checkout).

Config example:

    source:
      type: filesystem
      root: ./docs
      include: ["**/*.md", "**/*.txt", "**/*.rst"]
      path_style: relative   # how paths are recorded in your chunk metadata
"""
from __future__ import annotations

import fnmatch
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..models import SourceDoc
from ..connectors.base import SourceConnector

DEFAULT_INCLUDE = ["**/*.md", "**/*.txt", "**/*.rst", "**/*.html", "**/*.pdf"]


class FilesystemSource(SourceConnector):
    name = "filesystem"

    def __init__(self, root: str,
                 include: Optional[list[str]] = None,
                 path_style: str = "relative"):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"source root not found: {self.root}")
        self.include = include or DEFAULT_INCLUDE
        self.path_style = path_style
        self._git_root = self._find_git_root()
        self._git_times = self._load_git_times()

    def _find_git_root(self) -> Optional[Path]:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                return Path(out.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    # -- git integration -----------------------------------------------------
    def _load_git_times(self) -> dict[str, datetime]:
        """Map relative path -> last commit time, if root is inside a git repo."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "log", "--format=%ct", "--name-only"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode != 0:
                return {}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        times: dict[str, datetime] = {}
        current_ts: Optional[datetime] = None
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.isdigit():
                current_ts = datetime.fromtimestamp(int(line), tz=timezone.utc)
            elif current_ts and line not in times:  # first occurrence = latest commit
                times[line] = current_ts
        return times

    # -- interface -------------------------------------------------------------
    def _matches(self, rel: str) -> bool:
        for pat in self.include:
            if fnmatch.fnmatch(rel, pat):
                return True
            # fnmatch has no special '**' semantics: '**/*.md' would demand a
            # slash and silently exclude files at the root of the source dir.
            if pat.startswith("**/") and fnmatch.fnmatch(rel, pat[3:]):
                return True
        return False

    def _record_path(self, p: Path) -> str:
        if self.path_style == "absolute":
            return str(p)
        if self.path_style == "name":
            return p.name
        return str(p.relative_to(self.root))

    def fetch_documents(self) -> Iterable[SourceDoc]:
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.root))
            if rel.startswith(".git/") or not self._matches(rel):
                continue
            git_key = (str(p.relative_to(self._git_root))
                       if self._git_root else rel)
            modified = self._git_times.get(git_key) or datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc)
            yield SourceDoc(
                path=self._record_path(p),
                title=p.stem,
                last_modified=modified,
                exists=True,
            )

    # -- change summaries -------------------------------------------------------
    def _git(self, *args: str) -> Optional[str]:
        if not self._git_root:
            return None
        try:
            out = subprocess.run(["git", "-C", str(self._git_root), *args],
                                 capture_output=True, text=True, timeout=30)
            return out.stdout if out.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def describe_change(self, doc_path: str, since: datetime) -> Optional[str]:
        """Summarize what changed in a source doc since `since` (embedding time).

        Returns e.g.:
          "1 commit since embedding (+1/-1 lines); latest: 'refund window 14->30 days'"
        or None when git history is unavailable.
        """
        if not self._git_root:
            return None
        full = Path(doc_path) if Path(doc_path).is_absolute() else self.root / doc_path
        try:
            rel = str(full.resolve().relative_to(self._git_root))
        except ValueError:
            return None

        log = self._git("log", "--format=%H %ct %s", "--", rel)
        if not log:
            return None
        commits = []  # newest first: (sha, ts, subject)
        for line in log.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                commits.append((parts[0], int(parts[1]),
                                parts[2] if len(parts) > 2 else ""))
        since_ts = since.timestamp()
        newer = [c for c in commits if c[1] > since_ts]
        older = [c for c in commits if c[1] <= since_ts]
        if not newer:
            return None
        base = older[0][0] if older else None  # the version that was embedded

        stat = ""
        if base:
            shortstat = self._git("diff", "--shortstat", base, "HEAD", "--", rel)
            if shortstat and shortstat.strip():
                ins = del_ = 0
                for tok in shortstat.split(","):
                    tok = tok.strip()
                    if "insertion" in tok:
                        ins = int(tok.split()[0])
                    elif "deletion" in tok:
                        del_ = int(tok.split()[0])
                stat = f" (+{ins}/-{del_} lines)"

        latest_subject = newer[0][2][:80]
        n = len(newer)
        return (f"{n} commit{'s' if n != 1 else ''} since embedding{stat}; "
                f"latest: '{latest_subject}'")
