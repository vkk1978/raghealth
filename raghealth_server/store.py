"""raghealth-server storage: SQLite, zero-ops.

Stores only what agents push — findings metadata and scores, a few KB per
snapshot. A year of daily scans for 100 customers fits in a few hundred MB,
which is the entire point: hosting cost stays near zero.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from typing import Any, Optional

DB_PATH = os.environ.get("RAGHEALTH_DB", "raghealth_server.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  api_key TEXT NOT NULL UNIQUE,
  dashboard_token TEXT NOT NULL UNIQUE,
  slack_webhook TEXT,
  alert_score_drop REAL DEFAULT 5.0,      -- alert if score falls by >= this
  alert_min_score REAL DEFAULT 70.0,      -- alert if score falls below this
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  kb TEXT NOT NULL,
  scanned_at TEXT NOT NULL,
  freshness_score REAL NOT NULL,
  payload TEXT NOT NULL,                  -- full sanitized JSON from the agent
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(workspace_id, kb, created_at);
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------ workspaces --
def create_workspace(conn, name: str, slack_webhook: Optional[str] = None) -> dict:
    api_key = "rh_" + secrets.token_urlsafe(32)
    token = secrets.token_urlsafe(16)
    conn.execute(
        "INSERT INTO workspaces (name, api_key, dashboard_token, slack_webhook, created_at)"
        " VALUES (?,?,?,?,?)", (name, api_key, token, slack_webhook, time.time()))
    conn.commit()
    return {"name": name, "api_key": api_key, "dashboard_token": token}


def workspace_by_key(conn, api_key: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM workspaces WHERE api_key = ?",
                        (api_key,)).fetchone()


def workspace_by_token(conn, token: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM workspaces WHERE dashboard_token = ?",
                        (token,)).fetchone()


def set_slack_webhook(conn, name: str, webhook: str) -> None:
    conn.execute("UPDATE workspaces SET slack_webhook = ? WHERE name = ?",
                 (webhook, name))
    conn.commit()


# ------------------------------------------------------------- snapshots --
def insert_snapshot(conn, workspace_id: int, payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO snapshots (workspace_id, kb, scanned_at, freshness_score,"
        " payload, created_at) VALUES (?,?,?,?,?,?)",
        (workspace_id, payload.get("kb", "default"), payload["scanned_at"],
         float(payload["freshness_score"]), json.dumps(payload), time.time()))
    conn.commit()
    return cur.lastrowid


def previous_snapshot(conn, workspace_id: int, kb: str,
                      before_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT payload FROM snapshots WHERE workspace_id=? AND kb=? AND id<?"
        " ORDER BY id DESC LIMIT 1", (workspace_id, kb, before_id)).fetchone()
    return json.loads(row["payload"]) if row else None


def history(conn, workspace_id: int, kb: str, limit: int = 90) -> list[dict]:
    rows = conn.execute(
        "SELECT scanned_at, freshness_score FROM snapshots"
        " WHERE workspace_id=? AND kb=? ORDER BY id DESC LIMIT ?",
        (workspace_id, kb, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def latest_per_kb(conn, workspace_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT kb, MAX(id) AS mid FROM snapshots WHERE workspace_id=?"
        " GROUP BY kb", (workspace_id,)).fetchall()
    out = []
    for r in rows:
        snap = conn.execute("SELECT payload FROM snapshots WHERE id=?",
                            (r["mid"],)).fetchone()
        out.append(json.loads(snap["payload"]))
    return out
