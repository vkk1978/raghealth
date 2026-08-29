"""Synthetic demo: a fictional company knowledge base with realistic rot.

`raghealth demo` runs the full check suite against this data so anyone can
see a report in 5 seconds without connecting a database.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from .models import Chunk, SearchHit, SourceDoc
from .connectors.base import SourceConnector, VectorStoreConnector
from .scanner import scan

NOW = datetime.now(timezone.utc)
DIM = 64
random.seed(42)


def _vec(seed: int, jitter: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    if jitter:
        v = [x + random.gauss(0, jitter) for x in v]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _chunks() -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(id_, source, days_old, text, seed, jitter=0.0):
        chunks.append(Chunk(
            id=id_, source_path=source, content=text,
            embedding=_vec(seed, jitter),
            embedded_at=NOW - timedelta(days=days_old)))

    # Fresh, healthy docs
    for i in range(6):
        add(f"onb-{i}", "handbook/onboarding.md", 3,
            f"Onboarding step {i}: set up your laptop and accounts.", seed=100 + i)
    for i in range(5):
        add(f"sec-{i}", "policies/security.md", 2,
            f"Security policy section {i}: use the password manager.", seed=200 + i)

    # STALE: refund policy embedded 45 days ago; source edited 5 days ago
    for i in range(8):
        add(f"ref-{i}", "policies/refund-policy.md", 45,
            f"Refunds are available within 14 days of purchase (section {i}).",
            seed=300 + i)

    # STALE (mild): pricing embedded 10 days ago, edited 6 days ago
    for i in range(4):
        add(f"pri-{i}", "products/pricing.md", 10,
            f"The Team plan costs $99/month (detail {i}).", seed=400 + i)

    # ORPHANS: old API v1 doc was deleted from the repo
    for i in range(5):
        add(f"api1-{i}", "engineering/api-v1-reference.md", 120,
            f"API v1 endpoint {i}: POST /v1/legacy — deprecated auth flow.",
            seed=500 + i)

    # CONFLICT: two versions of the vacation policy in the index,
    # nearly identical embeddings, different sources
    add("vac-old", "hr/vacation-policy-2024.md", 200,
        "Employees receive 15 days of paid vacation per year.", seed=600)
    add("vac-new", "hr/vacation-policy.md", 4,
        "Employees receive 20 days of paid vacation per year.", seed=600, jitter=0.02)

    # Redundant duplicate within one source (double ingestion)
    add("dup-a", "handbook/onboarding.md", 3,
        "Welcome to the company! This handbook covers everything you need.", seed=700)
    add("dup-b", "handbook/onboarding.md", 3,
        "Welcome to the company! This handbook covers everything you need.", seed=700, jitter=0.01)

    # Unlinked chunk — no source metadata at all
    chunks.append(Chunk(id="mystery-1", content="Some pasted text nobody can trace.",
                        embedding=_vec(800), embedded_at=NOW - timedelta(days=60)))
    return chunks


def _sources() -> list[SourceDoc]:
    return [
        SourceDoc("handbook/onboarding.md", "Onboarding Guide",
                  NOW - timedelta(days=20)),
        SourceDoc("policies/security.md", "Security Policy",
                  NOW - timedelta(days=30)),
        SourceDoc("policies/refund-policy.md", "Refund Policy",
                  NOW - timedelta(days=5)),          # edited AFTER embedding → stale
        SourceDoc("products/pricing.md", "Pricing",
                  NOW - timedelta(days=6)),          # edited after embedding → stale
        SourceDoc("hr/vacation-policy.md", "Vacation Policy",
                  NOW - timedelta(days=4)),
        SourceDoc("hr/vacation-policy-2024.md", "Vacation Policy (2024)",
                  NOW - timedelta(days=200), exists=False),  # archived → orphan
        # api-v1-reference.md deleted entirely → orphan (not listed here)
        SourceDoc("legal/terms-of-service.md", "Terms of Service",
                  NOW - timedelta(days=15)),         # never ingested → coverage gap
        SourceDoc("products/roadmap.md", "Product Roadmap",
                  NOW - timedelta(days=1)),          # never ingested → coverage gap
    ]


class _DemoStore(VectorStoreConnector):
    name = "demo-store"
    supports_search = True

    def __init__(self):
        self._chunks = _chunks()

    def search(self, vector, k: int = 5):
        def cos(a, b):
            return sum(x * y for x, y in zip(a, b))  # vectors are normalized
        scored = sorted(((cos(vector, c.embedding), c) for c in self._chunks
                         if c.embedding), key=lambda t: -t[0])[:k]
        return [SearchHit(chunk_id=c.id, score=s, source_path=c.source_path,
                          content=c.content) for s, c in scored]

    def fetch_chunks(self, include_embeddings=True, limit=None):
        data = self._chunks[:limit] if limit else self._chunks
        for c in data:
            yield c

    def count(self):
        return len(self._chunks)


class _DemoSource(SourceConnector):
    name = "demo-docs"

    def fetch_documents(self):
        yield from _sources()


DEMO_CANARIES = {  # query keyword -> the seed its embedding points at
    "refund-policy": ("What is our refund policy?", 300),
    "vacation-days": ("How many vacation days do employees get?", 600),
    "security": ("What are the security requirements?", 200),
}


def demo_embedder(text: str) -> list[float]:
    for key, (_q, seed) in DEMO_CANARIES.items():
        if key.split("-")[0] in text.lower():
            return _vec(seed)
    return _vec(999)


def build_demo_report():
    from .canary import Canary, CanarySet
    cset = CanarySet(k=4, canaries=[Canary(id=k, query=q)
                                    for k, (q, _s) in DEMO_CANARIES.items()])
    return scan(_DemoStore(), _DemoSource(), grace_days=7.0,
                duplicate_threshold=0.97,
                canary_set=cset, embedder=demo_embedder)
