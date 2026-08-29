"""Google Drive source connector (experimental).

Lists files with their modifiedTime via the Drive v3 API. Two auth modes:

  1. Service account (recommended for automation) — share the Drive
     folder with the service account's email:

       source:
         type: gdrive
         service_account_file: ./sa.json     # requires: pip install google-auth
         folder_id: 1AbC...                  # optional; omit = all visible files
         path_style: id                      # id | name

  2. Raw OAuth access token (short-lived; fine for one-off scans):

       source:
         type: gdrive
         access_token: env:GDRIVE_TOKEN

Marked experimental: API surface is stable but this connector has not yet
been exercised against a large production Drive. Please report issues.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import SourceDoc
from ..connectors.base import SourceConnector

API = "https://www.googleapis.com/drive/v3/files"
FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, trashed)"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_FOLDER = "application/vnd.google-apps.folder"


class GDriveSource(SourceConnector):
    name = "gdrive"

    def __init__(self, service_account_file: Optional[str] = None,
                 access_token: Optional[str] = None,
                 folder_id: Optional[str] = None,
                 path_style: str = "id",
                 session=None):
        try:
            import requests
        except ImportError as e:
            raise ImportError("gdrive connector requires requests") from e
        self._session = session or requests.Session()
        self.folder_id = folder_id
        self.path_style = path_style
        self._token = self._auth(service_account_file, access_token)

    def _auth(self, sa_file: Optional[str], token: Optional[str]) -> str:
        if token:
            if token.startswith("env:"):
                val = os.environ.get(token[4:])
                if not val:
                    raise ValueError(f"env var {token[4:]} is not set")
                return val
            return token
        if sa_file:
            try:
                from google.oauth2 import service_account
                from google.auth.transport.requests import Request
            except ImportError as e:
                raise ImportError(
                    "service account auth requires google-auth: "
                    "pip install 'raghealth[gdrive]' or pip install google-auth") from e
            creds = service_account.Credentials.from_service_account_file(
                sa_file, scopes=[SCOPE])
            creds.refresh(Request())
            return creds.token
        raise ValueError("gdrive source needs service_account_file or access_token")

    def _list_page(self, page_token: Optional[str]) -> dict:
        params = {"fields": FIELDS, "pageSize": 1000,
                  "q": "trashed = false"}
        if self.folder_id:
            params["q"] += f" and '{self.folder_id}' in parents"
        if page_token:
            params["pageToken"] = page_token
        for attempt in range(4):
            r = self._session.get(API, params=params,
                                  headers={"Authorization": f"Bearer {self._token}"},
                                  timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Drive API: repeated rate limiting")

    def fetch_documents(self) -> Iterable[SourceDoc]:
        page_token = None
        while True:
            data = self._list_page(page_token)
            for f in data.get("files", []):
                if f.get("mimeType") == _FOLDER:
                    continue
                mt = f.get("modifiedTime")
                last_modified = (datetime.fromisoformat(mt.replace("Z", "+00:00"))
                                 .astimezone(timezone.utc) if mt else None)
                yield SourceDoc(
                    path=f["name"] if self.path_style == "name" else f["id"],
                    title=f.get("name"),
                    last_modified=last_modified,
                    exists=not f.get("trashed", False),
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                return
