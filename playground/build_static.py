"""Render every playground scenario to static HTML for GitHub Pages."""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS

from raghealth.report import render_html

OUT = Path(__file__).parent.parent / "docs"
OUT.mkdir(exist_ok=True)

cards = []
for key, sc in SCENARIOS.items():
    report = sc.run()
    (OUT / f"{key}.html").write_text(render_html(report), encoding="utf-8")
    points = "".join(f"<li>{html.escape(h)}</li>" for h in sc.headlines)
    color = ("#4ade80" if report.freshness_score >= 90
             else "#ffc555" if report.freshness_score >= 70 else "#ff5c5c")
    cards.append(f"""
<a class="card" href="{key}.html">
  <div class="row"><span class="icon">{sc.icon}</span>
    <h2>{html.escape(sc.title)}</h2>
    <span class="score" style="color:{color}">{report.freshness_score}%</span></div>
  <ul>{points}</ul>
  <span class="cta">open the full health report →</span>
</a>""")

index = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>raghealth — demo gallery</title><style>
:root{{--bg:#0f1117;--card:#1a1d27;--text:#e8eaf0;--dim:#8b90a0;--accent:#7c9cff}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,'Segoe UI',Roboto,sans-serif;max-width:920px;margin:auto;padding:44px 20px}}
h1{{font-size:24px}} .sub{{color:var(--dim);margin:8px 0 30px;max-width:640px}}
.card{{display:block;background:var(--card);border-radius:14px;padding:22px 24px;margin-bottom:18px;text-decoration:none;color:var(--text);border:1px solid transparent}}
.card:hover{{border-color:var(--accent)}}
.row{{display:flex;gap:12px;align-items:center}} .icon{{font-size:22px}}
h2{{font-size:16px;flex:1}} .score{{font-size:22px;font-weight:700}}
ul{{margin:10px 0 8px 20px;color:var(--dim);font-size:13px}}
.cta{{color:var(--accent);font-size:13px}}
code{{background:rgba(255,255,255,.06);padding:2px 7px;border-radius:6px}}
footer{{color:var(--dim);font-size:13px;margin-top:28px}}
</style></head><body>
<h1>🩺 raghealth — knowledge-base rot, made visible</h1>
<p class="sub">Four industries, four AI assistants confidently answering from
stale, deleted, or conflicting content — while every ops metric stays green.
Each report below is real raghealth output on a synthetic knowledge base.</p>
{''.join(cards)}
<footer>Scan your own (locally, read-only): <code>pip install raghealth &&
raghealth init && raghealth scan</code> · MIT · GitHub: vkk1978/raghealth</footer>
</body></html>"""
(OUT / "index.html").write_text(index, encoding="utf-8")
print(f"wrote {len(SCENARIOS) + 1} pages to {OUT}/")
