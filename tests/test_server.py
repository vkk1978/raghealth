"""Server + agent tests: ingest, auth, alerts, payload sanitization."""
import json
import os
import tempfile

os.environ["RAGHEALTH_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient

from raghealth_server import store
from raghealth_server.app import app
from raghealth.agent import build_push_payload
from raghealth.demo import build_demo_report

client = TestClient(app)
conn = store.connect()
WS = store.create_workspace(conn, "testws")


def _snapshot(score, findings=None, kb="kb1"):
    return {"kb": kb, "scanned_at": "2026-07-09T00:00:00+00:00",
            "freshness_score": score, "total_chunks": 10,
            "results": [{"check": "staleness", "stats": {"stale": len(findings or [])},
                         "findings": findings or []}]}


def test_auth_required():
    assert client.post("/api/v1/ingest", json=_snapshot(90)).status_code == 401
    assert client.post("/api/v1/ingest", json=_snapshot(90),
                       headers={"X-API-Key": "wrong"}).status_code == 401


def test_ingest_and_alert_rules():
    h = {"X-API-Key": WS["api_key"]}
    r1 = client.post("/api/v1/ingest", json=_snapshot(95), headers=h).json()
    assert r1["snapshot_id"] and r1["alerts"] == []
    # small dip: no alert
    r2 = client.post("/api/v1/ingest", json=_snapshot(93), headers=h).json()
    assert r2["alerts"] == []
    # big drop + new finding + below threshold: all three reasons
    bad = _snapshot(60, findings=[{"severity": "critical",
                                   "title": "stale x", "source_path": "x.md"}])
    r3 = client.post("/api/v1/ingest", json=bad, headers=h).json()
    reasons = " | ".join(r3["alerts"])
    assert "dropped" in reasons and "new finding" in reasons and "below" in reasons


def test_kb_isolation():
    h = {"X-API-Key": WS["api_key"]}
    # a different kb starts its own history: no drop alert vs kb1
    r = client.post("/api/v1/ingest", json=_snapshot(92, kb="kb2"), headers=h).json()
    assert r["alerts"] == []


def test_dashboard():
    h = {"X-API-Key": WS["api_key"]}
    client.post("/api/v1/ingest", json=_snapshot(88, kb="dash-kb"), headers=h)
    client.post("/api/v1/ingest", json=_snapshot(91, kb="dash-kb"), headers=h)
    ws = store.workspace_by_key(conn, WS["api_key"])
    r = client.get(f"/d/{ws['dashboard_token']}")
    assert r.status_code == 200
    assert "dash-kb" in r.text and "svg" in r.text and ">91%" in r.text.replace("91.0%", "91%")
    assert client.get("/d/nope").status_code == 404


def test_payload_sanitization():
    report = build_demo_report()  # NOT redacted — worst case for the agent
    payload = build_push_payload(report, kb="k")
    blob = json.dumps(payload)
    # whitelist serializer must still drop every content field
    assert "Refunds are available" not in blob
    assert "Welcome to the company" not in blob
    assert payload["freshness_score"] == report.freshness_score
    assert all("chunk_ids" not in f for r in payload["results"]
               for f in r["findings"])  # ids replaced by counts


if __name__ == "__main__":
    for name in sorted(list(globals())):
        if name.startswith("test_"):
            globals()[name]()
            print(f"✓ {name}")
    print("server tests passed")
