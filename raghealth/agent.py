"""raghealth agent — scheduled scans that push findings to a raghealth server.

Privacy model (the core architecture decision of the hosted product):
  - The agent runs in YOUR infrastructure, next to your DB and docs.
  - Only findings METADATA leaves: scores, stats, titles, source paths,
    severities, chunk counts. Never credentials, never vectors, never
    document content (content is stripped before push, always — the agent
    forces redact mode regardless of scan config).

Config (add to raghealth.yaml):

    push:
      url: https://health.yourteam.dev     # your raghealth-server
      api_key: env:RAGHEALTH_API_KEY
      kb: production-docs                  # knowledge base name on the dashboard
      canaries: canaries.yaml              # optional: include blast radius

Run once (cron-friendly) or on an interval:

    raghealth agent --once
    raghealth agent --interval 6h
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict
from typing import Optional

from .models import HealthReport

AGENT_VERSION = 1
MAX_FINDINGS_PER_CHECK = 200


def _resolve(v: Optional[str]) -> Optional[str]:
    if v and v.startswith("env:"):
        val = os.environ.get(v[4:])
        if not val:
            raise ValueError(f"environment variable {v[4:]} is not set")
        return val
    return v


def build_push_payload(report: HealthReport, kb: str) -> dict:
    """Sanitized snapshot. Chunk content never appears here: agent scans run
    with redact=True, and this serializer only copies whitelisted fields."""
    results = []
    for r in report.results:
        findings = []
        for f in r.findings[:MAX_FINDINGS_PER_CHECK]:
            findings.append({
                "severity": f.severity.value,
                "title": f.title,
                "detail": f.detail,
                "source_path": f.source_path,
                "chunk_count": len(f.chunk_ids),
                "data": {k: v for k, v in f.data.items()
                         if isinstance(v, (str, int, float, bool))
                         and not k.startswith("excerpt")},
            })
        results.append({"check": r.check, "summary": r.summary,
                        "stats": r.stats, "findings": findings,
                        "truncated": len(r.findings) > MAX_FINDINGS_PER_CHECK})
    return {
        "agent_version": AGENT_VERSION,
        "kb": kb,
        "scanned_at": report.scanned_at.isoformat(),
        "store": report.store_name,
        "source": report.source_name,
        "total_chunks": report.total_chunks,
        "total_sources": report.total_sources,
        "freshness_score": report.freshness_score,
        "link_stats": {k: v for k, v in report.link_stats.items()
                       if k in ("distinct_paths", "matched", "rate_pct", "by_method")},
        "results": results,
    }


def push(url: str, api_key: str, payload: dict) -> dict:
    import requests
    r = requests.post(url.rstrip("/") + "/api/v1/ingest",
                      json=payload,
                      headers={"X-API-Key": api_key},
                      timeout=60)
    r.raise_for_status()
    return r.json()


def run_agent(args) -> int:
    from .config import build_source, build_store, load_config, parse_scan_options
    from .scanner import scan

    cfg = load_config(args.config)
    push_cfg = cfg.get("push") or {}
    url = getattr(args, "url", None) or push_cfg.get("url")
    api_key = _resolve(getattr(args, "api_key", None) or push_cfg.get("api_key"))
    kb = getattr(args, "kb", None) or push_cfg.get("kb") or "default"
    if not url or not api_key:
        raise SystemExit("agent needs push.url and push.api_key in raghealth.yaml "
                         "(or --url/--api-key)")

    interval_s = _parse_interval(getattr(args, "interval", None))

    while True:
        store = build_store(dict(cfg["store"]))
        source = build_source(dict(cfg["source"]))
        try:
            opts = parse_scan_options(cfg)
            opts["redact"] = True  # forced: content never leaves this machine
            canaries_path = push_cfg.get("canaries")
            if canaries_path and cfg.get("embedder"):
                from .canary import CanarySet
                from .embedders import build_embedder
                opts["canary_set"] = CanarySet.load(canaries_path)
                opts["embedder"] = build_embedder(cfg["embedder"])
            report = scan(store, source, **opts)
        finally:
            store.close()
            source.close()

        payload = build_push_payload(report, kb)
        resp = push(url, api_key, payload)
        print(f"pushed snapshot: kb={kb} score={payload['freshness_score']}% "
              f"-> {url} (snapshot #{resp.get('snapshot_id')}, "
              f"alerts: {resp.get('alerts_sent', 0)})")

        if args.once or interval_s is None:
            return 0
        time.sleep(interval_s)


def _parse_interval(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    raw = raw.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(raw[-1])
    if mult:
        return float(raw[:-1]) * mult
    return float(raw)
