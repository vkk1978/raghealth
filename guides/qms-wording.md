# Wording for QMS procedures (ISO 9001 and related standards)

If your organization treats the retrieval index behind an AI assistant as a
document point of use (see article: "Your RAG assistant is a document
'point of use'"), here is paste-ready wording for a document-control
procedure, plus how raghealth outputs serve as records. Adapt numbering and
cadence to your QMS; this is a starting point, not legal or audit advice.

> **X.X Retrieval systems.** Where documented information is made available
> to personnel through an AI retrieval system (vector index), that system
> shall be treated as a point of use. The index shall be reviewed at least
> [monthly] to verify that (a) indexed content reflects the current
> approved revision of each source document, (b) content originating from
> obsolete or withdrawn documents is not retrievable, and (c) current
> controlled documents within the system's defined scope are present in
> the index. Review results and remediation actions shall be retained as
> documented information.

**Records:** run `raghealth scan --json --html --redact` on the defined
cadence. The dated report evidences the review (clause (a): staleness
findings; (b): orphan findings; (c): coverage findings); the fix queue plus
its follow-up scan evidence remediation. `--redact` keeps document content
out of the records themselves.
