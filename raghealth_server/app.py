"""raghealth-server: FastAPI app.

    POST /api/v1/ingest      (X-API-Key)   — agents push snapshots here
    GET  /d/{token}                        — shareable read-only dashboard
    GET  /healthz

Run:  python -m raghealth_server run --port 8080
Keys: python -m raghealth_server create-workspace acme
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import alerts, store

app = FastAPI(title="raghealth-server", docs_url=None, redoc_url=None)

BASE_URL = os.environ.get("RAGHEALTH_BASE_URL")  # e.g. https://health.yourteam.dev
MAX_BODY = 2_000_000  # 2 MB — snapshots are small by design


def _conn():
    return store.connect()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/v1/ingest")
async def ingest(request: Request, x_api_key: str = Header(default="")):
    conn = _conn()
    try:
        ws = store.workspace_by_key(conn, x_api_key)
        if not ws:
            raise HTTPException(401, "invalid API key")
        body = await request.body()
        if len(body) > MAX_BODY:
            raise HTTPException(413, "snapshot too large")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "invalid JSON")
        for field in ("scanned_at", "freshness_score", "results"):
            if field not in payload:
                raise HTTPException(422, f"missing field: {field}")

        kb = payload.get("kb", "default")
        snapshot_id = store.insert_snapshot(conn, ws["id"], payload)
        prev = store.previous_snapshot(conn, ws["id"], kb, snapshot_id)
        reasons = alerts.evaluate(prev, payload,
                                  score_drop=ws["alert_score_drop"],
                                  min_score=ws["alert_min_score"])
        sent = alerts.dispatch(ws, kb, payload, reasons, base_url=BASE_URL)
        return JSONResponse({"snapshot_id": snapshot_id,
                             "alerts": reasons, "alerts_sent": sent})
    finally:
        conn.close()


# ------------------------------------------------------------- dashboard --
_CSS = """
:root{--bg:#0f1117;--card:#1a1d27;--text:#e8eaf0;--dim:#8b90a0;
--red:#ff5c5c;--yellow:#ffc555;--green:#4ade80;--accent:#7c9cff}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;max-width:960px;margin:auto;padding:36px 20px}
h1{font-size:19px}.sub{color:var(--dim);font-size:13px;margin:4px 0 24px}
.kb{background:var(--card);border-radius:14px;padding:22px 24px;margin-bottom:20px}
.kb h2{font-size:16px}.row{display:flex;gap:26px;align-items:center;flex-wrap:wrap}
.score{font-size:44px;font-weight:700}
.stat b{display:block;font-size:19px}.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
svg.trend{background:rgba(255,255,255,.03);border-radius:8px}
.f{border-left:3px solid var(--dim);padding:6px 12px;margin:8px 0;font-size:13px;background:rgba(255,255,255,.02)}
.f.critical{border-color:var(--red)}.f.warning{border-color:var(--yellow)}
.f .d{color:var(--dim)}
.badge{font-size:10px;font-weight:700;text-transform:uppercase;padding:1px 7px;border-radius:20px;margin-right:6px}
.badge.critical{background:rgba(255,92,92,.15);color:var(--red)}
.badge.warning{background:rgba(255,197,85,.15);color:var(--yellow)}
.badge.info{background:rgba(139,144,160,.15);color:var(--dim)}
footer{color:var(--dim);font-size:12px;text-align:center;margin-top:26px}
"""


def _sparkline(points: list[dict], w: int = 420, h: int = 80) -> str:
    if not points:
        return ""
    scores = [p["freshness_score"] for p in points]
    n = len(scores)
    xs = [10 + (w - 20) * (i / max(n - 1, 1)) for i in range(n)]
    ys = [h - 12 - (h - 24) * (s / 100.0) for s in scores]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                    for i, (x, y) in enumerate(zip(xs, ys)))
    last_color = ("var(--green)" if scores[-1] >= 90
                  else "var(--yellow)" if scores[-1] >= 70 else "var(--red)")
    dots = (f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="4" fill="{last_color}"/>'
            if n else "")
    grid = "".join(
        f'<line x1="10" x2="{w-10}" y1="{h-12-(h-24)*g/100:.1f}" '
        f'y2="{h-12-(h-24)*g/100:.1f}" stroke="rgba(255,255,255,.06)"/>'
        for g in (50, 75, 100))
    return (f'<svg class="trend" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">{grid}'
            f'<path d="{path}" fill="none" stroke="var(--accent)" '
            f'stroke-width="2"/>{dots}</svg>')


def _kb_section(conn, ws, snap: dict) -> str:
    kb = snap.get("kb", "default")
    score = snap["freshness_score"]
    color = ("var(--green)" if score >= 90
             else "var(--yellow)" if score >= 70 else "var(--red)")
    hist = store.history(conn, ws["id"], kb)
    crit = warn = 0
    finding_rows = []
    for r in snap.get("results", []):
        for f in r.get("findings", []):
            sev = f.get("severity", "info")
            crit += sev == "critical"
            warn += sev == "warning"
    shown = 0
    for r in snap.get("results", []):
        for f in sorted(r.get("findings", []),
                        key=lambda f: {"critical": 0, "warning": 1}.get(
                            f.get("severity"), 2)):
            if shown >= 12:
                break
            finding_rows.append(
                f'<div class="f {f.get("severity","info")}">'
                f'<span class="badge {f.get("severity","info")}">'
                f'{f.get("severity","info")}</span>'
                f'{html.escape(f.get("title") or "")}'
                f'<div class="d">{html.escape((f.get("detail") or "")[:220])}</div></div>')
            shown += 1
    return f"""
<div class="kb">
  <h2>{html.escape(kb)}</h2>
  <div class="sub">last scan {html.escape(str(snap.get('scanned_at','')))} ·
    store {html.escape(str(snap.get('store','')))} ·
    {snap.get('total_chunks','?')} chunks · {snap.get('total_sources','?')} sources</div>
  <div class="row">
    <div class="score" style="color:{color}">{score}%</div>
    <div class="stat"><b style="color:var(--red)">{crit}</b><span>critical</span></div>
    <div class="stat"><b style="color:var(--yellow)">{warn}</b><span>warnings</span></div>
    {_sparkline(hist)}
  </div>
  {''.join(finding_rows)}
</div>"""


@app.get("/d/{token}", response_class=HTMLResponse)
def dashboard(token: str):
    conn = _conn()
    try:
        ws = store.workspace_by_token(conn, token)
        if not ws:
            raise HTTPException(404, "unknown dashboard")
        snaps = store.latest_per_kb(conn, ws["id"])
        body = ("".join(_kb_section(conn, ws, s) for s in snaps)
                or "<div class='kb'>no snapshots yet — run <b>raghealth agent --once</b></div>")
        now = datetime.now(timezone.utc)
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>raghealth — {html.escape(ws['name'])}</title><style>{_CSS}</style></head><body>
<h1>Knowledge base health — {html.escape(ws['name'])}</h1>
<div class="sub">read-only dashboard · generated {now:%Y-%m-%d %H:%M UTC}</div>
{body}
<footer>raghealth — freshness monitoring for RAG knowledge bases</footer>
</body></html>"""
    finally:
        conn.close()
