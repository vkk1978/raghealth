# Your RAG assistant is a document "point of use" — and your QMS doesn't know it exists

*For quality managers and engineering leaders whose organizations are
deploying AI assistants over controlled documents. The argument here is
deliberately narrow: not that AI is risky in general, but that one specific,
well-understood document-control requirement now has an uncontrolled surface.*

---

Document control is one of the most mature disciplines in quality
management. If your organization holds ISO 9001 certification — or IATF
16949, AS9100, ISO 13485, which inherit the same structure — you already
run a system for it: revision numbers, approval workflows, controlled
distribution, and procedures ensuring that when a document is superseded,
the old version is removed from circulation or clearly marked obsolete.

Clause 7.5.3 is explicit about the goal. Documented information must be
available and suitable for use where and when it is needed, and the
organization must prevent the unintended use of obsolete documents.
Auditors check this in practice: they walk the floor and verify that what
people are actually working from is the current revision. Obsolete documents
left in circulation are a classic nonconformance, and document control is
consistently among the most common audit-finding areas.

Every control in that system was designed around the distribution channels
that existed when it was written: paper binders, shared drives, the QMS
software, the intranet. Here is the development that quietly changed the
picture.

## A new distribution channel appeared

When an organization deploys a retrieval-augmented AI assistant — "ask the
maintenance bot," "search our SOPs in natural language" — the deployment
process typically copies the content of controlled documents into a vector
database. Each document is split into chunks; each chunk is converted to an
embedding; the assistant answers questions by retrieving the most relevant
chunks and composing a response from them.

That vector database is now a place where employees obtain work
instructions. A technician asking the assistant "what's the lockout/tagout
procedure for line 7?" and acting on the answer is using documented
information to do work. By any reasonable reading, the index is a **point of
use** — the same category as the binder on the shop floor or the PDF on the
intranet.

The difference is that the binder and the intranet are inside your document
control system, and the index almost certainly is not.

## How the index drifts out of control

The copy into the vector database happens at ingestion time. Unless the
pipeline was explicitly built to re-synchronize — and most first-generation
RAG deployments were not — nothing that happens to the source documents
afterward propagates. This produces failure modes that map one-to-one onto
the situations clause 7.5.3 exists to prevent:

**The revised procedure.** A safety review updates the lockout/tagout SOP.
The document control system does its job: new revision approved, old
revision withdrawn, distribution updated. The chunks embedded from the old
revision remain in the index, and — this is the technical crux — they remain
*excellent semantic matches* for lockout/tagout questions. Similarity search
has no concept of revision status. The assistant keeps describing the
superseded steps, with full confidence.

**The obsolete specification.** An engineering change order supersedes a
torque spec; Rev B is removed from all controlled locations. Its chunks are
still retrievable. Functionally, an obsolete document is still in
circulation at a point of use — precisely the condition your obsolete-
document procedure exists to prevent, occurring in a location that procedure
doesn't cover.

**Two revisions in circulation at once.** The new calibration procedure was
ingested, but the old revision's chunks were never removed. Both sit in the
index with near-identical embeddings. Which one the assistant works from can
vary with the phrasing of the question. An auditor's question — "how do you
ensure personnel use the current revision?" — has no good answer for this
channel.

**The document that never arrived.** A new work instruction was approved and
published but never ingested, so the assistant either can't answer or
answers from an older, related document. Availability "where and when it is
needed" fails silently.

None of this shows up in the metrics anyone watches. The assistant's uptime
is fine, its response quality *sounds* fine, and the document control system
reports full compliance — because the index isn't in its scope.

## What a control for this looks like

The encouraging part: this is one of the easier gaps to close, because
detection is a bookkeeping comparison, not a judgment call. A periodic check
needs two pieces of metadata per chunk — which document it came from, and
when it was embedded — and then compares the index against the document
system of record:

- chunks embedded before their source's latest approved revision (**stale**);
- chunks whose source document has been withdrawn or superseded
  (**obsolete content at a point of use**);
- near-identical chunks originating from different revisions
  (**multiple revisions in circulation**);
- current controlled documents with no presence in the index
  (**availability gap**).

Run on a schedule, the output is a findings report and a remediation list:
re-embed these, remove those, ingest these. Both artifacts are dated records
— which matters, because under 7.5.3 the natural way to treat this check is
as *evidence of a control operating*: retained reports demonstrating that
the retrieval channel is reviewed for revision currency and obsolete
content, the same way you'd evidence any other periodic document review.

Two honest boundaries on the claim. First, none of this replaces document
control — it extends an existing discipline to one new channel, nothing
more. Second, a health check is detective, not preventive; the preventive
fix is building deletion- and revision-propagation into the ingestion
pipeline, and organizations should do that too. The check is what tells you
whether it's working.

## If you want to try this

I maintain an open-source (MIT) tool, **raghealth**, that implements exactly
these checks: it connects read-only to the vector database (pgvector,
Chroma, Qdrant) and the document source (git, Notion, Google Drive, with
more connectors in progress), and produces the report and remediation queue
described above. There's an interactive demonstration of the manufacturing
scenario — the revised LOTO procedure, the superseded torque spec, the two
calibration revisions — at PLAYGROUND_URL, and the project is at GITHUB_URL.

But the recommendation stands independent of any tool: if your organization
has deployed, or is about to deploy, an AI assistant over controlled
documents, put the retrieval index on your document-control map. It is a
point of use. The clause you already comply with says what to do with those.
