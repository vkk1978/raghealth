"""Verifies every claim in guides/pipeline-metadata.md against the real
frameworks. Skips cleanly when the optional deps aren't installed; CI
installs them (see the guide-snippets job in ci.yml).

This suite exists because the guide promises copy-paste correctness — and
because writing it caught a real bug (root-level files excluded by
FilesystemSource glob matching)."""
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _need(*mods):
    missing = []
    for m in mods:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    return missing


def test_langchain_stamp_roundtrip():
    missing = _need("langchain_core", "langchain_chroma", "chromadb")
    if missing:
        print(f"  (skipped: {missing} not installed)")
        return
    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from raghealth.connectors.chroma import ChromaConnector
    from raghealth.sources.filesystem import FilesystemSource
    from raghealth.scanner import scan

    # the guide's snippet, verbatim
    def stamp(docs, source_path):
        now = datetime.now(timezone.utc).isoformat()
        for d in docs:
            d.metadata.update(source=source_path, embedded_at=now)
        return docs

    class FakeEmb:
        def embed_documents(self, texts):
            return [[float(len(t)), 1.0, 0.5] for t in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0, 0.5]

    with tempfile.TemporaryDirectory() as td:
        docs_dir = Path(td) / "docs"
        docs_dir.mkdir()
        (docs_dir / "refund-policy.md").write_text("# Refund policy")

        vs = Chroma(collection_name="guide", embedding_function=FakeEmb(),
                    persist_directory=str(Path(td) / "chroma"))
        vs.add_documents(stamp([Document(page_content="Refunds within 14 days")],
                               "docs/refund-policy.md"))

        store = ChromaConnector(collection="guide",
                                path=str(Path(td) / "chroma"),
                                source_path_key="source",
                                embedded_at_key="embedded_at")
        rep = scan(store, FilesystemSource(str(docs_dir)),
                   include_embeddings=False)
        stats = {r.check: r.stats for r in rep.results}
        assert stats["staleness"]["unknown_embed_time"] == 0
        assert stats["orphans"]["orphaned"] == 0
        assert rep.link_stats["rate_pct"] == 100.0
        assert rep.freshness_score == 100.0


def test_llamaindex_metadata_claims():
    missing = _need("llama_index.core")
    if missing:
        print(f"  (skipped: {missing} not installed)")
        return
    from llama_index.core import Document as LDoc, SimpleDirectoryReader
    from llama_index.core.node_parser import SentenceSplitter

    now = datetime.now(timezone.utc).isoformat()
    d = LDoc(text="Refunds within 14 days of purchase. " * 10,
             metadata={"source": "docs/refund-policy.md", "embedded_at": now})
    nodes = SentenceSplitter().get_nodes_from_documents([d])
    assert nodes and nodes[0].metadata["source"] == "docs/refund-policy.md"
    assert nodes[0].metadata["embedded_at"] == now

    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.md").write_text("# hello")
        docs = SimpleDirectoryReader(td).load_data()
        assert "file_path" in docs[0].metadata  # the guide's mapping claim


def test_introspection_detects_guide_key_names():
    from raghealth.introspect import detect_source_key, detect_timestamp_key
    now = datetime.now(timezone.utc).isoformat()
    metas = [{"file_path": f"/x/{i}.md", "embedded_at": now} for i in range(5)]
    assert detect_source_key(metas) == "file_path"
    assert detect_timestamp_key(metas) == "embedded_at"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"\n{len(fns)} guide tests passed")
