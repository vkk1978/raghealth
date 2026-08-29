# raghealth for product owners

You own an AI assistant that answers from company content. This page is the
non-technical version of what raghealth tells you and what to do with it.

**The one number: the freshness score.** It's the percentage of your
assistant's knowledge that is current and traceable to a live document.
100% means every chunk of indexed content matches a document that still
exists and hasn't been edited since. 76% means roughly a quarter of what
your assistant can retrieve is out of date, deleted, or duplicated — and it
will still be served confidently, because retrieval has no concept of
"current."

**What to ask your team** (copy-paste): *"Can you run raghealth against our
knowledge base and send me the HTML report? It's read-only and takes a few
minutes — pip install raghealth, raghealth init, raghealth scan."*

**How to read the report:** the score is the headline; the findings tell
you which documents are affected (e.g. "5 stale chunks from
'refund-policy' — source updated 12 days ago"). The fix queue attached to
it is for your engineers: it lists exactly what to re-embed, delete, or
ingest — usually an hour of pipeline work, not a project.

**What "good" looks like:** 90%+ and steady. The score is a KPI you can
put next to CSAT/deflection: re-check monthly, or run the monitoring server
for a trend chart and a Slack alert the week something goes stale.
