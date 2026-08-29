"""Alerting: compare a new snapshot to the previous one, notify on regression.

Rules (per workspace, configurable):
  1. score dropped by >= alert_score_drop points (default 5)
  2. score fell below alert_min_score (default 70)
  3. NEW findings appeared that weren't in the previous snapshot
     (matched by stable identity, reusing raghealth.diffing)

Channels: Slack incoming webhook (per workspace), SMTP email (server-wide
env config: RAGHEALTH_SMTP_HOST/PORT/USER/PASS/FROM/TO).
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from raghealth.diffing import diff_reports


def evaluate(prev: Optional[dict], new: dict,
             score_drop: float = 5.0, min_score: float = 70.0) -> list[str]:
    """Return a list of human-readable alert reasons (empty = healthy)."""
    reasons: list[str] = []
    new_score = float(new["freshness_score"])
    if prev is not None:
        prev_score = float(prev["freshness_score"])
        delta = new_score - prev_score
        if delta <= -score_drop:
            reasons.append(f"freshness score dropped {abs(delta):.1f} points "
                           f"({prev_score:.1f}% → {new_score:.1f}%)")
        d = diff_reports(prev, new)
        if d.new_findings:
            worst = [f for f in d.new_findings if f.get("severity") == "critical"]
            head = ", ".join((f.get("title") or "?") for f in d.new_findings[:3])
            reasons.append(
                f"{len(d.new_findings)} new finding(s)"
                + (f" ({len(worst)} critical)" if worst else "")
                + f": {head}")
    if new_score < min_score:
        reasons.append(f"freshness score {new_score:.1f}% is below the "
                       f"{min_score:.0f}% threshold")
    return reasons


def format_message(workspace: str, kb: str, new: dict, reasons: list[str],
                   dashboard_url: Optional[str] = None) -> str:
    lines = [f":rotating_light: raghealth alert — {workspace}/{kb}",
             f"freshness score: {new['freshness_score']}% "
             f"({new.get('total_chunks', '?')} chunks)"]
    lines += [f"• {r}" for r in reasons]
    if dashboard_url:
        lines.append(dashboard_url)
    return "\n".join(lines)


def send_slack(webhook_url: str, text: str, session=None) -> bool:
    import requests
    s = session or requests
    try:
        r = s.post(webhook_url, json={"text": text}, timeout=15)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("RAGHEALTH_SMTP_HOST")
    to = os.environ.get("RAGHEALTH_SMTP_TO")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("RAGHEALTH_SMTP_FROM", "raghealth@localhost")
    msg["To"] = to
    msg.set_content(body)
    try:
        port = int(os.environ.get("RAGHEALTH_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            if os.environ.get("RAGHEALTH_SMTP_USER"):
                s.starttls()
                s.login(os.environ["RAGHEALTH_SMTP_USER"],
                        os.environ.get("RAGHEALTH_SMTP_PASS", ""))
            s.send_message(msg)
        return True
    except Exception:
        return False


def dispatch(workspace_row, kb: str, new: dict, reasons: list[str],
             base_url: Optional[str] = None) -> int:
    """Send to all configured channels; return number of successful sends."""
    if not reasons:
        return 0
    dash = (f"{base_url.rstrip('/')}/d/{workspace_row['dashboard_token']}"
            if base_url else None)
    text = format_message(workspace_row["name"], kb, new, reasons, dash)
    sent = 0
    if workspace_row["slack_webhook"]:
        sent += 1 if send_slack(workspace_row["slack_webhook"], text) else 0
    sent += 1 if send_email(f"[raghealth] {workspace_row['name']}/{kb} alert",
                            text) else 0
    return sent
