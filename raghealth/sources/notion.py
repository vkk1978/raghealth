"""Notion source connector.

Uses the official Notion REST API (integration token) to list pages and their
last_edited_time. Pages are identified by page ID or URL — configure
`path_style` to match whatever your ingestion pipeline stored in chunk metadata.

Config example:

    source:
      type: notion
      token: env:NOTION_TOKEN        # read from environment
      path_style: id                 # id | url
      # optional: restrict to one database
      database_id: xxxxxxxxxxxxxxxx
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import SourceDoc
from ..connectors.base import SourceConnector

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _resolve_token(token: str) -> str:
    if token.startswith("env:"):
        val = os.environ.get(token[4:])
        if not val:
            raise ValueError(f"environment variable {token[4:]} is not set")
        return val
    return token


class NotionSource(SourceConnector):
    name = "notion"

    def __init__(self, token: str,
                 database_id: Optional[str] = None,
                 path_style: str = "id",
                 rate_limit_sleep: float = 0.35):
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise ImportError("notion connector requires requests: pip install requests") from e
        import requests
        self._requests = requests
        self.token = _resolve_token(token)
        self.database_id = database_id
        self.path_style = path_style
        self.sleep = rate_limit_sleep  # Notion allows ~3 req/s

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }

    def _post(self, url: str, payload: dict) -> dict:
        for attempt in range(4):
            r = self._requests.post(url, headers=self._headers(), json=payload, timeout=30)
            if r.status_code == 429:  # rate limited — respect Retry-After
                time.sleep(float(r.headers.get("Retry-After", 1)))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Notion API: repeated rate limiting")

    def _iter_pages(self) -> Iterable[dict]:
        if self.database_id:
            url = f"{API}/databases/{self.database_id}/query"
            payload: dict = {"page_size": 100}
        else:
            url = f"{API}/search"
            payload = {"page_size": 100,
                       "filter": {"property": "object", "value": "page"}}
        cursor = None
        while True:
            if cursor:
                payload["start_cursor"] = cursor
            data = self._post(url, payload)
            yield from data.get("results", [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            time.sleep(self.sleep)

    @staticmethod
    def _title_of(page: dict) -> Optional[str]:
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                parts = prop.get("title") or []
                if parts:
                    return "".join(t.get("plain_text", "") for t in parts)
        return None

    def fetch_documents(self) -> Iterable[SourceDoc]:
        for page in self._iter_pages():
            edited = page.get("last_edited_time")
            last_modified = None
            if edited:
                last_modified = datetime.fromisoformat(
                    edited.replace("Z", "+00:00")).astimezone(timezone.utc)
            path = page.get("url") if self.path_style == "url" else page["id"]
            yield SourceDoc(
                path=path,
                title=self._title_of(page),
                last_modified=last_modified,
                exists=not page.get("archived", False),
            )
