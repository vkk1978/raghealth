#!/usr/bin/env python3
"""Render a raghealth scan JSON as GitHub Actions job-summary markdown.

Usage: python action_summary.py raghealth-report.json
Prints markdown to stdout (the action appends it to $GITHUB_STEP_SUMMARY).
Content-safe: only titles, details, stats, and paths — chunk text never
appears in scan JSON details since v0.5 (excerpts live in data keys, which
this renderer ignores).
"""
from __future__ import annotations

import json
import sys

SEV = {"critical": "🔴", "warning": "🟡", "info": "⚪"}
MAX_FINDINGS = 10


def main(path: str) -> None:
    r = json.load(open(path))
    score = r["freshness_score"]
    badge = "🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴")
    crit = sum(1 for res in r["results"] for f in res["findings"]
               if f["severity"] == "critical")
    warn = sum(1 for res in r["results"] for f in res["findings"]
               if f["severity"] == "warning")

    print(f"## {badge} Knowledge base health: **{score}%**")
    print(f"\n{r['total_chunks']} chunks · {r['total_sources']} source docs · "
          f"store `{r['store_name']}` · source `{r['source_name']}` · "
          f"**{crit}** critical / **{warn}** warnings")

    ls = r.get("link_stats") or {}
    if ls.get("distinct_paths"):
        print(f"\nSource linking: **{ls.get('rate_pct')}%** of "
              f"{ls.get('distinct_paths')} distinct chunk paths matched")

    print("\n| check | summary |\n|---|---|")
    for res in r["results"]:
        summary = (res["summary"] or "").replace("|", "\\|")
        print(f"| **{res['check']}** | {summary} |")

    findings = [(res["check"], f) for res in r["results"]
                for f in res["findings"]]
    findings.sort(key=lambda cf: {"critical": 0, "warning": 1}.get(
        cf[1]["severity"], 2))
    if findings:
        print(f"\n<details><summary>Top findings "
              f"({min(len(findings), MAX_FINDINGS)} of {len(findings)})"
              f"</summary>\n")
        for check, f in findings[:MAX_FINDINGS]:
            title = f["title"].replace("|", "\\|")
            detail = (f.get("detail") or "").replace("\n", " ")[:200]
            print(f"- {SEV.get(f['severity'], '⚪')} `{check}` **{title}**  ")
            print(f"  {detail}")
        print("\n</details>")
    else:
        print("\n✅ No findings.")

    print("\n*Reports: `raghealth-report.html` · `raghealth-fix-queue.json` "
          "(attach with `actions/upload-artifact` to keep them).*")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "raghealth-report.json")
