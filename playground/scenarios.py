"""Industry scenario datasets for the raghealth playground.

Each scenario is a synthetic knowledge base with story-driven rot: stale
policies, zombie documents, conflicting versions, and coverage gaps that
mirror a real vertical's failure modes. Built on in-memory connectors so the
playground runs anywhere (Streamlit Cloud, HF Spaces) with no database.

Every scenario also defines canary queries wired to the synthetic vector
space, so blast-radius scoring works end to end.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from raghealth.connectors.base import SourceConnector, VectorStoreConnector
from raghealth.models import Chunk, SearchHit, SourceDoc
from raghealth.scanner import scan
from raghealth.canary import Canary, CanarySet

NOW = datetime.now(timezone.utc)
DIM = 64


def _vec(seed: int, jitter: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    if jitter:
        jr = random.Random(seed * 7 + 1)
        v = [x + jr.gauss(0, jitter) for x in v]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class MemoryStore(VectorStoreConnector):
    supports_search = True

    def __init__(self, name: str, chunks: list[Chunk]):
        self.name = name
        self._chunks = chunks

    def count(self) -> int:
        return len(self._chunks)

    def fetch_chunks(self, include_embeddings=True, limit=None) -> Iterable[Chunk]:
        yield from (self._chunks[:limit] if limit else self._chunks)

    def search(self, vector, k: int = 5):
        def cos(a, b):
            return sum(x * y for x, y in zip(a, b))
        top = sorted(((cos(vector, c.embedding), c) for c in self._chunks
                      if c.embedding), key=lambda t: -t[0])[:k]
        return [SearchHit(chunk_id=c.id, score=s, source_path=c.source_path,
                          content=c.content) for s, c in top]


class MemorySource(SourceConnector):
    def __init__(self, name: str, docs: list[SourceDoc]):
        self.name = name
        self._docs = docs

    def fetch_documents(self) -> Iterable[SourceDoc]:
        yield from self._docs


@dataclass
class Scenario:
    key: str
    title: str
    icon: str
    blurb: str                 # the story shown above the report
    store: MemoryStore
    source: MemorySource
    canaries: CanarySet
    canary_seeds: dict[str, int]
    headlines: list[str] = field(default_factory=list)  # talking points

    def embedder(self, text: str) -> list[float]:
        for cid, seed in self.canary_seeds.items():
            if any(w in text.lower() for w in cid.split("-")):
                return _vec(seed)
        return _vec(9999)

    def run(self):
        return scan(self.store, self.source,
                    canary_set=self.canaries, embedder=self.embedder)


# --------------------------------------------------------------- builders --
def _mk(id_, source, days_old, text, seed, jitter=0.0) -> Chunk:
    return Chunk(id=id_, source_path=source, content=text,
                 embedding=_vec(seed, jitter),
                 embedded_at=NOW - timedelta(days=days_old))


def support() -> Scenario:
    chunks = []
    # STALE + high blast radius: refund policy changed 14 -> 30 days
    for i in range(6):
        chunks.append(_mk(f"ref-{i}", "help/refund-policy",
                          40, f"Customers may request a refund within 14 days "
                              f"of purchase (section {i}).", 300 + i))
    # fresh shipping doc
    for i in range(4):
        chunks.append(_mk(f"shp-{i}", "help/shipping-rates", 2,
                          f"Standard shipping is $4.99, section {i}.", 500 + i))
    # ZOMBIE: 2025 holiday returns article deleted, chunks retrievable
    for i in range(3):
        chunks.append(_mk(f"hol-{i}", "help/holiday-returns-2025", 200,
                          f"Extended holiday returns until Jan 31 (part {i}).",
                          300 + i, jitter=0.03))  # near refund space -> retrieved!
    # CONFLICT: two cancellation policies
    chunks.append(_mk("can-old", "help/cancellation-2025",
                      300, "Cancel anytime with a 30-day notice period.", 700))
    chunks.append(_mk("can-new", "help/cancellation",
                      5, "Cancel anytime, effective immediately.", 700, jitter=0.02))
    docs = [
        SourceDoc("help/refund-policy", "Refund Policy",
                  NOW - timedelta(days=6)),                      # edited after embed
        SourceDoc("help/shipping-rates", "Shipping Rates", NOW - timedelta(days=9)),
        SourceDoc("help/cancellation", "Cancellation Policy", NOW - timedelta(days=5)),
        SourceDoc("help/cancellation-2025", "Cancellation (2025)",
                  NOW - timedelta(days=300), exists=False),      # archived
        SourceDoc("help/pricing", "Pricing", NOW - timedelta(days=3)),  # never ingested
        # holiday-returns-2025 deleted entirely -> orphan
    ]
    cs = CanarySet(k=4, canaries=[
        Canary("refund-return", "How do I get a refund or return an item?"),
        Canary("cancel-subscription", "How do I cancel my subscription?"),
        Canary("shipping-cost", "How much does shipping cost?")])
    return Scenario(
        key="support", title="Customer Support / Help Center", icon="🎧",
        blurb=("SupportCo's AI assistant answers from a help-center knowledge "
               "base. Six weeks ago the refund window changed from 14 to 30 "
               "days — but the chunks were embedded before the edit. A deleted "
               "2025 holiday-returns article is still in the index and gets "
               "retrieved for refund questions. Two cancellation policies "
               "coexist. The bot is confidently wrong three different ways, "
               "and every dashboard is green."),
        store=MemoryStore("supportco-vectors", chunks),
        source=MemorySource("supportco-helpcenter", docs),
        canaries=cs,
        canary_seeds={"refund-return": 300, "cancel-subscription": 700,
                      "shipping-cost": 500},
        headlines=[
            "Bot quotes a 14-day refund window; the real policy says 30",
            "A DELETED holiday-returns article is retrieved at high rank for refund questions",
            "Two cancellation policies -> contradictory answers ticket-to-ticket",
        ])


def fintech() -> Scenario:
    chunks = []
    # STALE regulatory policy: KYC threshold updated
    for i in range(5):
        chunks.append(_mk(f"kyc-{i}", "policies/kyc-requirements", 60,
                          f"Enhanced due diligence required above $10,000 "
                          f"(clause {i}).", 100 + i))
    # CONFLICT: AML procedure v2 still indexed next to v3
    chunks.append(_mk("aml-v2", "procedures/aml-screening-v2", 250,
                      "Screen wires above $3,000 against the watchlist.", 200))
    chunks.append(_mk("aml-v3", "procedures/aml-screening", 8,
                      "Screen ALL wires against the consolidated watchlist.",
                      200, jitter=0.02))
    # ZOMBIE: retired product term sheet
    for i in range(3):
        chunks.append(_mk(f"ts-{i}", "products/flexi-loan-terms", 400,
                          f"FlexiLoan APR 8.9%, early-exit fee waived (s.{i})",
                          400 + i))
    # fresh onboarding doc
    for i in range(4):
        chunks.append(_mk(f"ob-{i}", "policies/account-opening", 3,
                          f"Account opening checklist item {i}.", 600 + i))
    docs = [
        SourceDoc("policies/kyc-requirements", "KYC Requirements",
                  NOW - timedelta(days=12)),                 # updated post-embed
        SourceDoc("procedures/aml-screening", "AML Screening Procedure",
                  NOW - timedelta(days=8)),
        SourceDoc("procedures/aml-screening-v2", "AML Screening v2 (superseded)",
                  NOW - timedelta(days=250), exists=False),
        SourceDoc("policies/account-opening", "Account Opening",
                  NOW - timedelta(days=15)),
        SourceDoc("policies/sanctions-update-2026", "2026 Sanctions Update",
                  NOW - timedelta(days=4)),                  # never ingested!
        # flexi-loan-terms deleted -> orphan
    ]
    cs = CanarySet(k=4, canaries=[
        Canary("kyc-documentation", "What KYC documentation is required?"),
        Canary("wire-screening", "When must wires be screened against the watchlist?")])
    return Scenario(
        key="fintech", title="Financial Services / Compliance", icon="🏦",
        blurb=("FinBank's internal assistant helps staff answer policy "
               "questions. The KYC threshold was updated by a regulatory "
               "change 12 days ago — the assistant still cites the old "
               "clause. A superseded AML procedure sits in the index beside "
               "its replacement, so screening guidance depends on retrieval "
               "luck. A retired loan product's term sheet is still quotable. "
               "The 2026 sanctions update was never ingested at all. In this "
               "vertical, stale answers aren't embarrassing — they're "
               "reportable."),
        store=MemoryStore("finbank-vectors", chunks),
        source=MemorySource("finbank-policies", docs),
        canaries=cs,
        canary_seeds={"kyc-documentation": 100, "wire-screening": 200},
        headlines=[
            "Assistant cites a superseded KYC threshold 12 days after a regulatory change",
            "Superseded AML procedure retrieved alongside the current one — screening guidance depends on luck",
            "The 2026 sanctions update exists but was never ingested",
        ])


def devdocs() -> Scenario:
    chunks = []
    # ZOMBIE + high blast radius: deprecated v1 auth docs, deleted from repo
    for i in range(5):
        chunks.append(_mk(f"v1-{i}", "docs/authentication-v1", 300,
                          f"Authenticate with ?api_key= in the query string "
                          f"(v1, part {i}).", 100 + i))
    # fresh v2 auth docs — but embedded BEFORE the latest edit (rotation added)
    for i in range(4):
        chunks.append(_mk(f"v2-{i}", "docs/authentication", 20,
                          f"Use Bearer tokens in the Authorization header "
                          f"(part {i}).", 100 + i, jitter=0.03))
    # STALE: rate limits doubled last week
    for i in range(3):
        chunks.append(_mk(f"rl-{i}", "docs/rate-limits", 30,
                          f"Rate limit: 60 requests/minute (part {i}).", 300 + i))
    # fresh webhooks doc
    for i in range(3):
        chunks.append(_mk(f"wh-{i}", "docs/webhooks", 2,
                          f"Webhook delivery and retries, part {i}.", 500 + i))
    docs = [
        SourceDoc("docs/authentication", "Authentication",
                  NOW - timedelta(days=5)),                  # key-rotation section added
        SourceDoc("docs/rate-limits", "Rate Limits", NOW - timedelta(days=7)),
        SourceDoc("docs/webhooks", "Webhooks", NOW - timedelta(days=10)),
        SourceDoc("docs/sdk-v3-migration", "SDK v3 Migration Guide",
                  NOW - timedelta(days=6)),                  # never ingested
        # authentication-v1 deleted from repo -> orphan
    ]
    cs = CanarySet(k=4, canaries=[
        Canary("authentication-authenticate", "How do I authenticate API requests?"),
        Canary("rate-limits", "What are the API rate limits?")])
    return Scenario(
        key="devdocs", title="Developer Documentation", icon="📚",
        blurb=("DevAPI's docs bot answers developer questions. The v1 auth "
               "pages were deleted from the docs repo months ago — but their "
               "chunks are still indexed, and because auth questions are "
               "semantically close, the bot still recommends putting API keys "
               "in query strings. Rate limits doubled last week; the bot "
               "quotes the old ones. The SDK v3 migration guide shipped but "
               "was never ingested, so the bot shrugs at the one question "
               "everyone is asking this month."),
        store=MemoryStore("devapi-vectors", chunks),
        source=MemorySource("devapi-docs-repo", docs),
        canaries=cs,
        canary_seeds={"authentication-authenticate": 100, "rate-limits": 300},
        headlines=[
            "Bot recommends DELETED v1 auth (api_key in query string) for auth questions",
            "Rate limits doubled last week; bot still quotes the old numbers",
            "SDK v3 migration guide exists but was never ingested",
        ])


def hr() -> Scenario:
    chunks = []
    chunks.append(_mk("vac-old", "hr/vacation-policy-2024", 300,
                      "Employees receive 15 days of paid vacation.", 100))
    chunks.append(_mk("vac-new", "hr/vacation-policy", 6,
                      "Employees receive 20 days of paid vacation.", 100,
                      jitter=0.02))
    for i in range(4):   # STALE: remote-work policy updated after embedding
        chunks.append(_mk(f"rw-{i}", "hr/remote-work", 50,
                          f"Remote work requires manager approval (s.{i}).",
                          300 + i))
    for i in range(4):   # fresh benefits doc
        chunks.append(_mk(f"bn-{i}", "hr/benefits-overview", 3,
                          f"Benefits overview part {i}.", 500 + i))
    docs = [
        SourceDoc("hr/vacation-policy", "Vacation Policy", NOW - timedelta(days=6)),
        SourceDoc("hr/vacation-policy-2024", "Vacation Policy (2024)",
                  NOW - timedelta(days=300), exists=False),
        SourceDoc("hr/remote-work", "Remote Work Policy",
                  NOW - timedelta(days=10)),
        SourceDoc("hr/benefits-overview", "Benefits Overview",
                  NOW - timedelta(days=8)),
        SourceDoc("hr/parental-leave", "Parental Leave Policy",
                  NOW - timedelta(days=5)),                  # never ingested
    ]
    cs = CanarySet(k=3, canaries=[
        Canary("vacation-days", "How many vacation days do I get?"),
        Canary("remote-work", "What is the remote work policy?")])
    return Scenario(
        key="hr", title="HR / Internal Policies", icon="🧑‍💼",
        blurb=("PeopleOps' HR assistant answers employee questions. The 2024 "
               "vacation policy (15 days) was archived when the new one (20 "
               "days) shipped — but its chunks were never removed, so the "
               "assistant's answer depends on which version wins retrieval "
               "that day. The remote-work policy changed after embedding. "
               "Parental leave was published but never ingested. Employees "
               "notice inconsistency fast — and screenshot it."),
        store=MemoryStore("peopleops-vectors", chunks),
        source=MemorySource("peopleops-notion", docs),
        canaries=cs,
        canary_seeds={"vacation-days": 100, "remote-work": 300},
        headlines=[
            "'How many vacation days?' — the answer depends on which policy version wins retrieval",
            "Remote-work policy changed after embedding; assistant cites the old rule",
            "Parental leave policy published but never ingested",
        ])


def manufacturing() -> Scenario:
    chunks = []
    # STALE + high blast radius: lockout/tagout updated after a safety review
    for i in range(5):
        chunks.append(_mk(f"loto-{i}", "sops/lockout-tagout", 55,
                          f"LOTO procedure step {i}: single padlock per "
                          f"energy source.", 100 + i))
    # ZOMBIE: superseded torque spec removed per ECO, chunks remain
    for i in range(3):
        chunks.append(_mk(f"tq-{i}", "specs/torque-spec-rev-b", 200,
                          f"Flange bolts: torque to 85 Nm (Rev B, s.{i}).",
                          300 + i))
    # CONFLICT: calibration procedure Rev C and Rev D both indexed
    chunks.append(_mk("cal-c", "sops/calibration-rev-c", 180,
                      "Calibrate pressure gauges every 12 months.", 500))
    chunks.append(_mk("cal-d", "sops/calibration", 7,
                      "Calibrate pressure gauges every 6 months.", 500,
                      jitter=0.02))
    # fresh housekeeping SOP
    for i in range(4):
        chunks.append(_mk(f"hk-{i}", "sops/5s-housekeeping", 3,
                          f"5S housekeeping standard, area {i}.", 700 + i))
    docs = [
        SourceDoc("sops/lockout-tagout", "Lockout/Tagout Procedure",
                  NOW - timedelta(days=10)),               # revised post-embed
        SourceDoc("sops/calibration", "Calibration Procedure (Rev D)",
                  NOW - timedelta(days=7)),
        SourceDoc("sops/calibration-rev-c", "Calibration Procedure (Rev C)",
                  NOW - timedelta(days=180), exists=False),  # superseded
        SourceDoc("sops/5s-housekeeping", "5S Housekeeping",
                  NOW - timedelta(days=12)),
        SourceDoc("manuals/press-line-7", "Press Line 7 Manual",
                  NOW - timedelta(days=5)),                # never ingested
        # torque-spec-rev-b removed per ECO -> orphan
    ]
    cs = CanarySet(k=4, canaries=[
        Canary("lockout-tagout", "What is the lockout tagout procedure?"),
        Canary("torque-spec", "What torque spec applies to the flange bolts?")])
    return Scenario(
        key="manufacturing", title="Manufacturing / Quality (ISO 9001)",
        icon="🏭",
        blurb=("PlantCo's maintenance assistant answers from SOPs and specs. "
               "The lockout/tagout procedure was revised after a safety "
               "review — the assistant still describes the old steps. A "
               "torque spec superseded by an engineering change order is "
               "still retrievable, and two calibration revisions coexist in "
               "the index. ISO 9001 clause 7.5.3 requires preventing the "
               "unintended use of obsolete documents at points of use; a "
               "retrieval index is a point of use that document control "
               "doesn't cover yet."),
        store=MemoryStore("plantco-vectors", chunks),
        source=MemorySource("plantco-qms-docs", docs),
        canaries=cs,
        canary_seeds={"lockout-tagout": 100, "torque-spec": 300},
        headlines=[
            "Lockout/tagout answer reflects the pre-revision procedure",
            "A torque spec superseded by ECO is still retrievable (obsolete document at a point of use)",
            "Calibration Rev C and Rev D both indexed — the interval answer depends on retrieval",
        ])


def healthcare() -> Scenario:
    chunks = []
    # STALE: discharge checklist revised per updated guidance
    for i in range(4):
        chunks.append(_mk(f"dc-{i}", "policies/discharge-checklist", 45,
                          f"Discharge checklist item {i} (2025 revision).",
                          100 + i))
    # ZOMBIE: withdrawn infection-control protocol still indexed
    for i in range(3):
        chunks.append(_mk(f"ic-{i}", "protocols/infection-control-2024", 300,
                          f"Contact precautions protocol (2024), part {i}.",
                          300 + i))
    # CONFLICT: two versions of the visitor policy
    chunks.append(_mk("vis-old", "policies/visitor-policy-2025", 250,
                      "Two visitors per patient, 8am-6pm.", 500))
    chunks.append(_mk("vis-new", "policies/visitor-policy", 6,
                      "Open visiting hours; two visitors at bedside.", 500,
                      jitter=0.02))
    # fresh staffing policy
    for i in range(4):
        chunks.append(_mk(f"st-{i}", "policies/staffing-escalation", 2,
                          f"Staffing escalation pathway step {i}.", 700 + i))
    docs = [
        SourceDoc("policies/discharge-checklist", "Discharge Checklist",
                  NOW - timedelta(days=9)),                 # revised post-embed
        SourceDoc("policies/visitor-policy", "Visitor Policy",
                  NOW - timedelta(days=6)),
        SourceDoc("policies/visitor-policy-2025", "Visitor Policy (2025)",
                  NOW - timedelta(days=250), exists=False),
        SourceDoc("policies/staffing-escalation", "Staffing Escalation",
                  NOW - timedelta(days=11)),
        SourceDoc("policies/formulary-update-q3", "Q3 Formulary Update",
                  NOW - timedelta(days=4)),                 # never ingested
        # infection-control-2024 withdrawn -> orphan
    ]
    cs = CanarySet(k=4, canaries=[
        Canary("discharge-checklist", "What is on the discharge checklist?"),
        Canary("infection-control-precautions",
               "What are the contact precautions under infection control?")])
    return Scenario(
        key="healthcare", title="Healthcare / Clinical Operations", icon="🏥",
        blurb=("A hospital's internal assistant helps staff look up policies "
               "and procedures — not diagnose. The discharge checklist was "
               "revised per updated guidance after its chunks were embedded. "
               "A withdrawn 2024 infection-control protocol is still "
               "retrievable, and two visitor policies coexist. Healthcare "
               "already runs formal document control because acting on "
               "superseded clinical documents is a recognized risk; content "
               "moving into retrieval systems needs the same discipline."),
        store=MemoryStore("mednet-vectors", chunks),
        source=MemorySource("mednet-policy-system", docs),
        canaries=cs,
        canary_seeds={"discharge-checklist": 100,
                      "infection-control-precautions": 300},
        headlines=[
            "Discharge checklist answer predates the latest revision",
            "A WITHDRAWN 2024 infection-control protocol is still retrievable for precaution questions",
            "Q3 formulary update was published but never ingested",
        ])


SCENARIOS = {s.key: s for s in (support(), fintech(), devdocs(), hr(),
                                manufacturing(), healthcare())}
