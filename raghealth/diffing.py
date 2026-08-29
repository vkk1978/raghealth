"""`raghealth diff old.json new.json` — what changed between two scans.

This is the habit-forming feature: run a scan weekly, diff against last
week, see only the delta. Also the CI story: fail a deploy if health
regressed.

Findings are matched by a stable identity key, not by title text (titles
embed counts that legitimately change):
  staleness/orphans/coverage → (check, source_path)
  duplicates                 → (check, sorted chunk_ids)
  anything else              → (check, title)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _key(check: str, f: dict) -> tuple:
    if check == "duplicates":
        return (check, tuple(sorted(f.get("chunk_ids") or [])))
    if f.get("source_path"):
        return (check, f["source_path"])
    return (check, f.get("title"))


def _index(report: dict) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in report.get("results", []):
        for f in r.get("findings", []):
            out[_key(r["check"], f)] = {**f, "check": r["check"]}
    return out


@dataclass
class ScanDiff:
    old_score: float
    new_score: float
    new_findings: list[dict] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)
    persisting: list[dict] = field(default_factory=list)
    stat_deltas: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    @property
    def score_delta(self) -> float:
        return round(self.new_score - self.old_score, 1)

    @property
    def regressed(self) -> bool:
        return bool(self.new_findings) or self.score_delta < 0


_TRACKED_STATS = [("staleness", "stale"), ("staleness", "stale_pct"),
                  ("orphans", "orphaned"), ("orphans", "unlinked"),
                  ("coverage", "coverage_pct"),
                  ("duplicates", "cross_source_pairs")]


def diff_reports(old: dict, new: dict) -> ScanDiff:
    oi, ni = _index(old), _index(new)
    d = ScanDiff(old_score=float(old.get("freshness_score", 0)),
                 new_score=float(new.get("freshness_score", 0)))
    for k, f in ni.items():
        (d.persisting if k in oi else d.new_findings).append(f)
    d.resolved = [f for k, f in oi.items() if k not in ni]

    old_stats = {r["check"]: r.get("stats", {}) for r in old.get("results", [])}
    new_stats = {r["check"]: r.get("stats", {}) for r in new.get("results", [])}
    for check, stat in _TRACKED_STATS:
        a = old_stats.get(check, {}).get(stat)
        b = new_stats.get(check, {}).get(stat)
        if a is not None and b is not None and a != b:
            d.stat_deltas[f"{check}.{stat}"] = (a, b)
    return d


def load_report(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def render_diff_terminal(d: ScanDiff, old_path: str, new_path: str) -> None:
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    arrow = "▲" if d.score_delta > 0 else ("▼" if d.score_delta < 0 else "→")
    color = "green" if d.score_delta > 0 else ("red" if d.score_delta < 0 else "dim")
    console.print(Panel(
        f"freshness score: {d.old_score}% → [bold {color}]{d.new_score}% "
        f"({arrow} {abs(d.score_delta)})[/]\n"
        f"[green]{len(d.resolved)} resolved[/] · "
        f"[red]{len(d.new_findings)} new[/] · "
        f"[dim]{len(d.persisting)} persisting[/]",
        title=f"raghealth diff — {old_path} → {new_path}",
        border_style=color if d.score_delta else "cyan"))

    if d.new_findings:
        console.print("\n[bold red]NEW since last scan[/]")
        for f in d.new_findings:
            console.print(f"  [red]+[/] [{f['check']}] {f['title']}")
    if d.resolved:
        console.print("\n[bold green]RESOLVED[/]")
        for f in d.resolved:
            console.print(f"  [green]✓[/] [{f['check']}] {f['title']}")
    if d.stat_deltas:
        console.print("\n[bold]metric changes[/]")
        for k, (a, b) in d.stat_deltas.items():
            worse = (b > a) ^ k.endswith("coverage_pct")
            c = "red" if worse else "green"
            console.print(f"  {k}: {a} → [{c}]{b}[/]")
    if not (d.new_findings or d.resolved or d.stat_deltas):
        console.print("\n[dim]no changes between scans[/]")
