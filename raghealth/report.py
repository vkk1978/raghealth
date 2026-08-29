"""Report rendering: terminal, JSON, and a shareable single-file HTML report.

The HTML report is deliberately screenshot-friendly — a big score, alarming
red numbers, and per-source breakdowns. The report IS the marketing.
"""
from __future__ import annotations

import html
import json
from dataclasses import asdict

from .models import HealthReport, Severity

SEV_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


# ---------------------------------------------------------------- terminal --
def render_terminal(report: HealthReport) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    score = report.freshness_score
    color = "green" if score >= 90 else ("yellow" if score >= 70 else "red")
    console.print(Panel(
        f"[bold {color}]{score}%[/] of chunks are fresh and linked to a live source\n"
        f"[dim]{report.total_chunks} chunks · {report.total_sources} source docs · "
        f"store: {report.store_name} · source: {report.source_name} · "
        f"{report.scanned_at:%Y-%m-%d %H:%M UTC}[/]"
        + (f"\n[dim]source linking: {report.link_stats.get('rate_pct')}% of "
           f"{report.link_stats.get('distinct_paths')} distinct chunk paths matched "
           f"({', '.join(f'{k}:{v}' for k, v in (report.link_stats.get('by_method') or {}).items())})[/]"
           if report.link_stats.get('distinct_paths') else ""),
        title="raghealth — knowledge base health", border_style=color))

    for r in report.results:
        console.print(f"\n[bold underline]{r.check.upper()}[/]  {r.summary}")
        if not r.findings:
            console.print("  [green]✓ no issues[/]")
            continue
        t = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        t.add_column("sev", width=8)
        t.add_column("finding")
        for f in sorted(r.findings, key=lambda f: SEV_ORDER[f.severity])[:15]:
            sev_style = {"critical": "red", "warning": "yellow", "info": "dim"}[f.severity.value]
            t.add_row(f"[{sev_style}]{f.severity.value}[/]", f"{f.title}\n[dim]{f.detail}[/]")
        console.print(t)
        if len(r.findings) > 15:
            console.print(f"  [dim]… and {len(r.findings) - 15} more (see HTML/JSON report)[/]")


# -------------------------------------------------------------------- json --
def render_json(report: HealthReport) -> str:
    def default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        if isinstance(o, Severity):
            return o.value
        return str(o)
    payload = asdict(report)
    payload["freshness_score"] = report.freshness_score
    return json.dumps(payload, indent=2, default=default)


# -------------------------------------------------------------------- html --
_CSS = """
:root{--bg:#0f1117;--card:#1a1d27;--text:#e8eaf0;--dim:#8b90a0;
--red:#ff5c5c;--yellow:#ffc555;--green:#4ade80;--accent:#7c9cff;}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,'Segoe UI',Roboto,sans-serif;padding:40px 20px;max-width:880px;margin:auto}
h1{font-size:20px;font-weight:600;letter-spacing:.3px}
.sub{color:var(--dim);font-size:13px;margin:4px 0 28px}
.score-card{background:var(--card);border-radius:14px;padding:28px;display:flex;gap:28px;align-items:center;margin-bottom:26px}
.score{font-size:56px;font-weight:700}
.stats{display:flex;gap:22px;flex-wrap:wrap}
.stat b{display:block;font-size:22px}
.stat span{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
section{background:var(--card);border-radius:14px;padding:22px 24px;margin-bottom:18px}
section h2{font-size:14px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);margin-bottom:6px}
.summary{color:var(--dim);font-size:14px;margin-bottom:14px}
.finding{border-left:3px solid var(--dim);padding:8px 14px;margin:10px 0;background:rgba(255,255,255,.02);border-radius:0 8px 8px 0}
.finding.critical{border-color:var(--red)}
.finding.warning{border-color:var(--yellow)}
.finding .t{font-weight:600;font-size:14px}
.finding .d{color:var(--dim);font-size:13px;margin-top:2px}
.badge{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;padding:2px 8px;border-radius:20px;margin-right:8px}
.badge.critical{background:rgba(255,92,92,.15);color:var(--red)}
.badge.warning{background:rgba(255,197,85,.15);color:var(--yellow)}
.badge.info{background:rgba(139,144,160,.15);color:var(--dim)}
.ok{color:var(--green);font-size:14px}
footer{color:var(--dim);font-size:12px;text-align:center;margin-top:30px}
"""


def render_html(report: HealthReport) -> str:
    score = report.freshness_score
    color = "var(--green)" if score >= 90 else ("var(--yellow)" if score >= 70 else "var(--red)")
    crit = sum(r.critical_count for r in report.results)
    warn = sum(r.warning_count for r in report.results)

    sections = []
    for r in report.results:
        rows = []
        for f in sorted(r.findings, key=lambda f: SEV_ORDER[f.severity])[:50]:
            rows.append(
                f'<div class="finding {f.severity.value}">'
                f'<span class="badge {f.severity.value}">{f.severity.value}</span>'
                f'<span class="t">{html.escape(f.title)}</span>'
                f'<div class="d">{html.escape(f.detail)}'
                + (f"<br>&ldquo;{html.escape(f.data['excerpt_a'])}&rdquo; ≈ "
                   f"&ldquo;{html.escape(f.data['excerpt_b'])}&rdquo;"
                   if "excerpt_a" in f.data else "")
                + '</div></div>')
        body = "".join(rows) or '<div class="ok">✓ no issues found</div>'
        more = (f'<div class="summary">… and {len(r.findings) - 50} more findings '
                f'in the JSON report</div>' if len(r.findings) > 50 else "")
        sections.append(
            f'<section><h2>{html.escape(r.check)}</h2>'
            f'<div class="summary">{html.escape(r.summary)}</div>{body}{more}</section>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>raghealth report</title><style>{_CSS}</style></head><body>
<h1>Knowledge Base Health Report</h1>
<div class="sub">store: {html.escape(report.store_name)} · source: {html.escape(report.source_name)}
 · scanned {report.scanned_at:%Y-%m-%d %H:%M UTC}
{f"<br>source linking: {report.link_stats.get('rate_pct')}% of {report.link_stats.get('distinct_paths')} distinct chunk paths matched" if report.link_stats.get('distinct_paths') else ""}</div>
<div class="score-card">
  <div class="score" style="color:{color}">{score}%</div>
  <div class="stats">
    <div class="stat"><b>{report.total_chunks}</b><span>chunks</span></div>
    <div class="stat"><b>{report.total_sources}</b><span>source docs</span></div>
    <div class="stat"><b style="color:var(--red)">{crit}</b><span>critical</span></div>
    <div class="stat"><b style="color:var(--yellow)">{warn}</b><span>warnings</span></div>
  </div>
</div>
{''.join(sections)}
<footer>generated by raghealth — freshness monitoring for RAG knowledge bases<br>not your pipeline? send this report to whoever runs your ingestion — the fix queue tells them exactly what to re-embed, delete, and ingest</footer>
</body></html>"""
