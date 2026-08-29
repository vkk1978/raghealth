"""raghealth playground — interactive demos of RAG knowledge-base rot.

Deploy free:
  Streamlit Community Cloud: point it at this repo, main file
    playground/app.py, requirements file playground/requirements.txt
  Hugging Face Spaces: create a Streamlit Space, same files.

Everything runs in-memory on synthetic scenario data. No credentials are
accepted anywhere — real scans happen locally via `pip install raghealth`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

from raghealth.queue import build_fix_queue  # noqa: E402
from raghealth.report import render_html  # noqa: E402

st.set_page_config(page_title="raghealth playground", page_icon="🩺",
                   layout="wide")

SEV_ICON = {"critical": "🔴", "warning": "🟡", "info": "⚪"}
CHECK_LABEL = {
    "staleness": "Staleness — source edited after chunks were embedded",
    "orphans": "Orphans — chunks whose source no longer exists",
    "coverage": "Coverage — documents that exist but were never ingested",
    "duplicates": "Conflicts & duplicates — near-identical chunks",
    "blast_radius": "Blast radius — flagged content actually being retrieved",
}


@st.cache_data(show_spinner="scanning knowledge base…")
def run_scenario(key: str):
    report = SCENARIOS[key].run()
    return report


with st.sidebar:
    st.title("🩺 raghealth")
    st.caption("Health checks for RAG knowledge bases — find the stale, "
               "orphaned, and conflicting chunks silently poisoning answers.")
    choice = st.radio(
        "Pick an industry scenario",
        list(SCENARIOS.keys()),
        format_func=lambda k: f"{SCENARIOS[k].icon} {SCENARIOS[k].title}")
    st.divider()
    st.markdown(
        "**This playground runs on synthetic data.** To scan your own "
        "knowledge base (locally — nothing leaves your machine):\n"
        "```bash\npip install raghealth\nraghealth init\nraghealth scan\n```")
    st.markdown("[GitHub](https://github.com/vkk1978/raghealth) · MIT licensed")

sc = SCENARIOS[choice]
report = run_scenario(choice)

st.header(f"{sc.icon} {sc.title}")
st.write(sc.blurb)

# ------------------------------------------------------------- scoreboard --
crit = sum(r.critical_count for r in report.results)
warn = sum(r.warning_count for r in report.results)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Freshness score", f"{report.freshness_score}%",
          help="% of chunks that are fresh and linked to a live source")
c2.metric("Chunks", report.total_chunks)
c3.metric("Source docs", report.total_sources)
c4.metric("Critical findings", crit)
c5.metric("Warnings", warn)

st.subheader("What the assistant is getting wrong right now")
for h in sc.headlines:
    st.markdown(f"- {h}")

# ---------------------------------------------------------------- findings --
tab_report, tab_blast, tab_fix, tab_how = st.tabs(
    ["🔍 Health report", "💥 Blast radius", "🛠 Fix queue", "🔌 Run it on yours"])

with tab_report:
    for r in report.results:
        if r.check == "blast_radius":
            continue
        with st.expander(f"{CHECK_LABEL.get(r.check, r.check)} — "
                         f"{len(r.findings)} finding(s)",
                         expanded=(r.critical_count > 0)):
            st.caption(r.summary)
            for f in r.findings:
                st.markdown(f"{SEV_ICON[f.severity.value]} **{f.title}**  \n"
                            f"<small>{f.detail}</small>",
                            unsafe_allow_html=True)
    st.download_button("⬇ download this as an HTML report",
                       render_html(report), file_name=f"{sc.key}_report.html",
                       mime="text/html")

with tab_blast:
    br = next((r for r in report.results if r.check == "blast_radius"), None)
    st.markdown(
        "A stale chunk nobody retrieves is housekeeping. A stale chunk at "
        "**rank 1** for a question users ask daily is actively poisoning "
        "answers. raghealth runs your canonical queries and checks which "
        "flagged chunks actually come back.")
    if br:
        st.caption(br.summary)
        st.markdown("**Canary queries for this scenario:**")
        for c in sc.canaries.canaries:
            st.code(c.query, language=None)
        for f in br.findings:
            st.markdown(f"{SEV_ICON[f.severity.value]} **{f.title}**  \n"
                        f"<small>{f.detail}</small>", unsafe_allow_html=True)
        if not br.findings:
            st.success("No flagged content appears in canary results.")

with tab_fix:
    st.markdown(
        "Scans end in an **actionable job**, not just a diagnosis: exactly "
        "which chunks to re-embed, delete, ingest, or review — feed it to "
        "your ingestion pipeline. No full re-index, no wasted embedding "
        "spend.")
    q = build_fix_queue(report)
    a, b = st.columns(2)
    a.metric("Actions", len(q["actions"]))
    b.metric("Chunks affected", q["summary"]["chunks_affected"])
    st.json(q, expanded=False)
    st.download_button("⬇ download fix queue (JSON)", json.dumps(q, indent=2),
                       file_name=f"{sc.key}_fix_queue.json",
                       mime="application/json")

with tab_how:
    st.markdown(f"""
**This scenario in your stack.** The rot you just explored is what raghealth
finds in real knowledge bases — read-only, no pipeline instrumentation:

```bash
pip install 'raghealth[pgvector]'   # or [chroma] / [qdrant]
raghealth init      # introspects your schema, writes raghealth.yaml
raghealth scan --html report.html --fix-queue queue.json
```

Works today with **pgvector / Supabase** (auto-detects LangChain's default
table), **Chroma**, and **Qdrant**; sources: **filesystem/git** (with
what-changed summaries), **Notion**, **Google Drive**.

For continuous monitoring — trend dashboards and a Slack alert the day a
policy doc changes without re-ingestion — there's a self-hostable agent +
server: your content never leaves your machine, only findings metadata does.

Privacy note about this playground: it accepts **no credentials and no
uploads by design**. Real scans run on your machine.
""")
