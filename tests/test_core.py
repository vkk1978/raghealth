"""Tests for path matching and introspection heuristics (no DB required)."""
import subprocess
import sys

from raghealth.matching import PathResolver, normalize, notion_id
from raghealth.introspect import (detect_source_key, detect_timestamp_key,
                                  looks_like_path, looks_like_timestamp)


# ------------------------------------------------------------- matching ----
def test_normalize():
    assert normalize("/data/docs/a.md") == "data/docs/a.md"
    assert normalize("file:///data/docs/a.md") == "data/docs/a.md"
    assert normalize("https://ex.com/wiki/Page?v=2#s") == "wiki/page"
    assert normalize("docs\\sub\\A.MD") == "docs/sub/a.md"
    assert normalize("./docs//a.md") == "docs/a.md"


def test_exact_and_normalized():
    r = PathResolver(["handbook/onboarding.md", "hr/vacation.md"])
    assert r.resolve("handbook/onboarding.md") == ("handbook/onboarding.md", "exact")
    assert r.resolve("./handbook//Onboarding.md")[1] == "normalized"


def test_suffix_absolute_vs_relative():
    r = PathResolver(["handbook/onboarding.md", "policies/refund.md"])
    m, how = r.resolve("/data/docs/handbook/onboarding.md")
    assert m == "handbook/onboarding.md" and how == "suffix"
    # reverse direction: source has the longer path
    r2 = PathResolver(["/srv/kb/policies/refund.md"])
    m2, how2 = r2.resolve("policies/refund.md")
    assert m2 == "/srv/kb/policies/refund.md" and how2 == "suffix"


def test_basename_unambiguous_and_ambiguous():
    r = PathResolver(["a/unique.md", "b/dup.md", "c/dup.md"])
    assert r.resolve("elsewhere/unique.md")[0] == "a/unique.md"
    m, how = r.resolve("x/dup.md")
    assert m is None and how == "ambiguous"


def test_unmatched_and_stats():
    r = PathResolver(["a.md"])
    assert r.resolve("nope.md") == (None, "unmatched")
    r.resolve("a.md")
    assert r.stats.total == 2 and r.stats.matched == 1 and r.stats.rate == 50.0


def test_notion_ids():
    nid = "0123456789abcdef0123456789abcdef"
    dashed = "01234567-89ab-cdef-0123-456789abcdef"
    url = f"https://www.notion.so/ws/My-Page-{nid}"
    assert notion_id(dashed) == nid and notion_id(url) == nid
    r = PathResolver([dashed])
    assert r.resolve(url)[0] == dashed


# --------------------------------------------------------- introspection ----
def test_looks_like():
    assert looks_like_path("/docs/a.md") and looks_like_path("https://x.com/p")
    assert not looks_like_path("hello world") and not looks_like_path(42)
    assert looks_like_timestamp("2026-07-01T00:00:00Z")
    assert looks_like_timestamp(1_750_000_000)
    assert not looks_like_timestamp("chunk 3") and not looks_like_timestamp(7)


def test_detect_keys_prefers_conventional_names():
    metas = [{"source": f"/d/{i}.md", "url": f"https://x/{i}",
              "ingested_at": "2026-06-01T00:00:00Z", "chunk": i} for i in range(10)]
    assert detect_source_key(metas) == "source"
    assert detect_timestamp_key(metas) == "ingested_at"


def test_detect_keys_absent():
    metas = [{"chunk": i, "note": "hi"} for i in range(5)]
    assert detect_source_key(metas) is None
    assert detect_timestamp_key(metas) is None


# ------------------------------------------------------------------ e2e ----
def test_demo_scan_and_redact():
    from raghealth.demo import _DemoStore, _DemoSource
    from raghealth.scanner import scan
    from raghealth.report import render_html, render_json
    rep = scan(_DemoStore(), _DemoSource(), redact=True)
    assert rep.total_chunks == 33 and 0 < rep.freshness_score < 100
    blob = render_html(rep) + render_json(rep)
    # demo content strings must not appear anywhere in redacted output
    assert "Refunds are available" not in blob
    assert "Welcome to the company" not in blob


def test_cli_demo_exits_zero():
    out = subprocess.run([sys.executable, "-m", "raghealth.cli", "demo"],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and "knowledge base health" in out.stdout


def test_cli_version_flag():
    """`raghealth --version` must return 0 and print a version string."""
    from raghealth import __version__
    out = subprocess.run([sys.executable, "-m", "raghealth.cli", "--version"],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0
    assert __version__ in out.stdout
    assert "raghealth" in out.stdout


def test_all_subcommand_help():
    """Every subcommand's --help must parse cleanly (catches broken argparse setup)."""
    for cmd in ["scan", "canary", "diff", "init", "agent", "demo"]:
        out = subprocess.run([sys.executable, "-m", "raghealth.cli", cmd, "--help"],
                             capture_output=True, text=True, encoding="utf-8")
        assert out.returncode == 0, f"{cmd} --help failed: {out.stderr}"
        assert "usage:" in out.stdout, f"{cmd} --help had no usage line"


def test_demo_html_survives_windows_cp1252_env():
    """Regression (v0.5.2): raghealth demo --html crashed on Windows because
    Path.write_text defaults to cp1252, which cannot encode U+26A0 (⚠).
    Simulate the crash environment by forcing PYTHONIOENCODING=cp1252."""
    import os, tempfile
    from pathlib import Path as _P
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    with tempfile.TemporaryDirectory() as td:
        out_html = _P(td, "r.html")
        out_json = _P(td, "r.json")
        r = subprocess.run(
            [sys.executable, "-m", "raghealth.cli", "demo",
             "--html", str(out_html), "--json", str(out_json)],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"CLI failed under cp1252: {r.stderr}"
        assert out_html.exists() and out_html.stat().st_size > 1000
        assert out_json.exists() and out_json.stat().st_size > 500
        # confirm the ⚠ glyph actually survives round-trip
        assert "⚠" in out_html.read_text(encoding="utf-8")




# ------------------------------------------------------------- v0.3 ----
def test_fix_queue():
    from raghealth.demo import _DemoStore, _DemoSource
    from raghealth.scanner import scan
    from raghealth.queue import build_fix_queue
    q = build_fix_queue(scan(_DemoStore(), _DemoSource()))
    assert q["version"] == 1
    acts = {a["action"] for a in q["actions"]}
    assert {"reembed", "delete", "ingest", "review_conflict"} <= acts
    assert q["summary"]["chunks_affected"] > 0
    # critical actions sort first
    assert q["actions"][0]["priority"] == "critical"
    # the "no source link" metadata-gap finding must NOT become a delete
    assert all(a.get("source_path") for a in q["actions"] if a["action"] == "delete")


def test_diff_identity_and_regression():
    from raghealth.diffing import diff_reports
    old = {"freshness_score": 80.0, "results": [
        {"check": "staleness", "stats": {"stale": 5},
         "findings": [{"title": "5 stale chunk(s) from 'a'", "source_path": "a.md"}]}]}
    # same source, different count in title -> persisting, not new
    new = {"freshness_score": 80.0, "results": [
        {"check": "staleness", "stats": {"stale": 7},
         "findings": [{"title": "7 stale chunk(s) from 'a'", "source_path": "a.md"},
                      {"title": "2 stale chunk(s) from 'b'", "source_path": "b.md"}]}]}
    d = diff_reports(old, new)
    assert len(d.persisting) == 1 and len(d.new_findings) == 1 and not d.resolved
    assert d.stat_deltas["staleness.stale"] == (5, 7)
    assert d.regressed
    d2 = diff_reports(new, old)
    assert len(d2.resolved) == 1 and not d2.regressed


def test_describe_change_no_git():
    import tempfile, pathlib
    from raghealth.sources.filesystem import FilesystemSource
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "a.md").write_text("x")
        src = FilesystemSource(td)
        # outside any git repo (tempdir) -> None, never crashes
        assert src.describe_change("a.md", datetime.now(timezone.utc)) is None




# ------------------------------------------------------------- v0.4 ----
def test_exposure_and_blast_radius():
    from raghealth.canary import compute_exposure, apply_blast_radius
    from raghealth.models import CheckResult, Finding, SearchHit, Severity
    results = {"q1": [SearchHit("stale1", 0.9, "a.md"), SearchHit("ok1", 0.8, "b.md")],
               "q2": [SearchHit("ok2", 0.9), SearchHit("stale1", 0.7, "a.md")]}
    exp = compute_exposure(results)
    assert exp["stale1"].best_rank == 1 and len(exp["stale1"].hits) == 2

    cr = CheckResult(check="staleness", summary="", findings=[
        Finding("staleness", Severity.WARNING, "stale a.md", "old",
                chunk_ids=["stale1"], source_path="a.md"),
        Finding("staleness", Severity.WARNING, "stale c.md", "old",
                chunk_ids=["never_retrieved"], source_path="c.md")])
    br = apply_blast_radius([cr], results)
    # exposed warning at rank 1 escalates to critical; unexposed stays put
    assert cr.findings[0].severity == Severity.CRITICAL
    assert cr.findings[1].severity == Severity.WARNING
    assert cr.findings[1].data["blast_radius"] == "not retrieved by any canary"
    assert br.stats["exposed_findings"] == 1
    assert "rank 1" in br.findings[0].title


def test_canary_overlap():
    from raghealth.canary import check_against_baseline
    from raghealth.models import SearchHit
    baseline = {"canaries": {"q": {"query": "x", "results": [
        {"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}, {"chunk_id": "d"}]}}}
    current = {"q": [SearchHit("a", 1), SearchHit("b", 1),
                     SearchHit("x", 1), SearchHit("y", 1)]}
    d = check_against_baseline(baseline, current)[0]
    assert d.overlap_pct == 50.0
    assert set(d.missing) == {"c", "d"} and set(d.new) == {"x", "y"}


def test_command_embedder():
    from raghealth.embedders import build_embedder
    e = build_embedder({"type": "command",
                        "cmd": "python3 -c \"import sys,json; t=sys.stdin.read(); print(json.dumps([float(len(t)), 1.0]))\""})
    assert e("abcd") == [4.0, 1.0]


def test_demo_includes_blast_radius():
    from raghealth.demo import build_demo_report
    rep = build_demo_report()
    br = next(r for r in rep.results if r.check == "blast_radius")
    assert br.stats["canaries"] == 3
    assert br.stats["exposed_findings"] >= 1  # demo has retrievable rot by design




def test_filesystem_root_level_files():
    """Regression: '**/*.md' must match files at the ROOT of the source dir
    (fnmatch has no ** semantics; root files were silently excluded)."""
    import tempfile, pathlib
    from raghealth.sources.filesystem import FilesystemSource
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "root-file.md").write_text("x")
        pathlib.Path(td, "sub").mkdir()
        pathlib.Path(td, "sub", "nested.md").write_text("y")
        paths = {d.path for d in FilesystemSource(td).fetch_documents()}
        assert paths == {"root-file.md", "sub/nested.md"}, paths


# ============================================================== v0.5.4 =====
# CLI error paths — every misconfiguration should exit non-zero with a
# friendly stderr message and NO Python traceback.


def _run_cli(args, cwd=None):
    return subprocess.run([sys.executable, "-m", "raghealth.cli", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8")


def test_scan_missing_config_shows_friendly_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = _run_cli(["scan"], cwd=td)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr
        assert "raghealth.yaml" in r.stderr


def test_scan_malformed_yaml_shows_friendly_error():
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "raghealth.yaml").write_text("store: {broken: [",
                                                       encoding="utf-8")
        r = _run_cli(["scan"], cwd=td)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr
        assert "YAML" in r.stderr or "yaml" in r.stderr


def test_scan_missing_store_section_shows_friendly_error():
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "raghealth.yaml").write_text(
            "source:\n  type: filesystem\n  root: ./docs\n", encoding="utf-8")
        r = _run_cli(["scan"], cwd=td)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr
        assert "store" in r.stderr


def test_diff_missing_report_shows_friendly_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = _run_cli(["diff", "nope1.json", "nope2.json"], cwd=td)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr


def test_diff_malformed_json_shows_friendly_error():
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        old = pathlib.Path(td, "old.json"); old.write_text("{ not valid json",
                                                            encoding="utf-8")
        new = pathlib.Path(td, "new.json"); new.write_text("{}", encoding="utf-8")
        r = _run_cli(["diff", str(old), str(new)])
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr
        assert "JSON" in r.stderr or "json" in r.stderr


def test_canary_baseline_missing_config_shows_friendly_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = _run_cli(["canary", "baseline"], cwd=td)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr, r.stderr


# ============================================================================
# Boundary / empty cases — stub connectors, no live services required.

from raghealth.connectors.base import VectorStoreConnector, SourceConnector


class _EmptyStore(VectorStoreConnector):
    name = "empty-store"
    def fetch_chunks(self, include_embeddings=True, limit=None):
        return iter([])
    def count(self):
        return 0


class _EmptySource(SourceConnector):
    name = "empty-source"
    def fetch_documents(self):
        return iter([])


def test_scan_empty_store_no_crash():
    from raghealth.scanner import scan
    from raghealth.report import render_json, render_html
    rep = scan(_EmptyStore(), _EmptySource())
    assert rep.total_chunks == 0
    assert rep.total_sources == 0
    assert isinstance(rep.freshness_score, (int, float))
    render_json(rep)   # must not raise
    render_html(rep)   # must not raise


def test_scan_all_fresh_reports_high_freshness():
    from datetime import datetime, timedelta, timezone
    from raghealth.models import Chunk, SourceDoc
    from raghealth.scanner import scan
    from raghealth.report import render_json, render_html
    now = datetime.now(timezone.utc)
    embedded = now - timedelta(days=1)
    class AllFreshStore(VectorStoreConnector):
        name = "fresh-store"
        def fetch_chunks(self, include_embeddings=True, limit=None):
            for i in range(3):
                yield Chunk(id=f"c-{i}", source_path=f"doc-{i}.md",
                            content=f"c{i}", embedding=[float(i)] * 8,
                            embedded_at=embedded)
        def count(self): return 3
    class AllFreshSource(SourceConnector):
        name = "fresh-source"
        def fetch_documents(self):
            for i in range(3):
                yield SourceDoc(f"doc-{i}.md", f"Doc {i}",
                                embedded - timedelta(days=1))
    rep = scan(AllFreshStore(), AllFreshSource())
    assert rep.total_chunks == 3
    assert rep.freshness_score > 90
    render_json(rep); render_html(rep)


def test_scan_empty_source_flags_orphans():
    from datetime import datetime, timedelta, timezone
    from raghealth.models import Chunk
    from raghealth.scanner import scan
    now = datetime.now(timezone.utc)
    class OrphanStore(VectorStoreConnector):
        name = "orphan-store"
        def fetch_chunks(self, include_embeddings=True, limit=None):
            yield Chunk(id="c-1", source_path="deleted.md", content="x",
                        embedding=[1.0] * 8,
                        embedded_at=now - timedelta(days=5))
        def count(self): return 1
    rep = scan(OrphanStore(), _EmptySource())
    assert rep.total_sources == 0
    orphans = next(r for r in rep.results if r.check == "orphans")
    assert orphans.stats.get("orphaned", 0) >= 1


# ============================================================================
# JSON schema stability — locks in the top-level output shape so users'
# CI integrations don't break silently when we refactor.


def test_json_schema_top_level_stable():
    import json
    from raghealth.demo import build_demo_report
    from raghealth.report import render_json
    d = json.loads(render_json(build_demo_report()))
    assert set(d.keys()) >= {"scanned_at", "store_name", "source_name",
                              "total_chunks", "total_sources",
                              "freshness_score", "results", "link_stats"}
    assert isinstance(d["freshness_score"], (int, float))
    assert isinstance(d["results"], list) and len(d["results"]) > 0
    for r in d["results"]:
        assert set(r.keys()) >= {"check", "summary", "findings", "stats"}
        for f in r["findings"]:
            assert set(f.keys()) >= {"severity", "title", "detail",
                                      "chunk_ids", "source_path", "data"}


# ============================================================================
# Metadata edge cases


def test_scan_missing_source_path_flagged_as_unlinked():
    from datetime import datetime, timedelta, timezone
    from raghealth.models import Chunk
    from raghealth.scanner import scan
    now = datetime.now(timezone.utc)
    class UnlinkedStore(VectorStoreConnector):
        name = "unlinked-store"
        def fetch_chunks(self, include_embeddings=True, limit=None):
            yield Chunk(id="orphan-1", content="mystery",
                        embedding=[1.0] * 8,
                        embedded_at=now - timedelta(days=10))
        def count(self): return 1
    rep = scan(UnlinkedStore(), _EmptySource())
    assert rep.total_chunks == 1
    orphans = next(r for r in rep.results if r.check == "orphans")
    # key: unlinked chunks are surfaced under 'orphans', not silently dropped
    assert (orphans.stats.get("unlinked", 0)
            + orphans.stats.get("no_source", 0)
            + orphans.stats.get("orphaned", 0)) >= 1


def test_scan_unicode_source_path_no_crash():
    from datetime import datetime, timedelta, timezone
    from raghealth.models import Chunk, SourceDoc
    from raghealth.scanner import scan
    from raghealth.report import render_html, render_json
    now = datetime.now(timezone.utc)
    weird = "docs/spaces and üñîçøde/policy—v2.md"
    class UStore(VectorStoreConnector):
        name = "u-store"
        def fetch_chunks(self, include_embeddings=True, limit=None):
            yield Chunk(id="u-1", source_path=weird, content="x",
                        embedding=[1.0] * 8,
                        embedded_at=now - timedelta(days=5))
        def count(self): return 1
    class USource(SourceConnector):
        name = "u-src"
        def fetch_documents(self):
            yield SourceDoc(weird, "policy v2", now - timedelta(days=1))
    rep = scan(UStore(), USource())
    assert rep.total_chunks == 1
    # both renderers must handle unicode paths without exceptions
    render_html(rep); render_json(rep)


# ============================================================================
# Idempotency — two scans of the same state must produce identical output
# (except the scanned_at timestamp). Guards against nondeterministic ordering.


def test_scan_is_idempotent():
    """Two scans of the same state produce identical deterministic parts.
    (Ages/timestamps drift by milliseconds between calls — compare structure only.)"""
    from raghealth.demo import _DemoStore, _DemoSource
    from raghealth.scanner import scan
    r1 = scan(_DemoStore(), _DemoSource())
    r2 = scan(_DemoStore(), _DemoSource())
    assert r1.total_chunks == r2.total_chunks
    assert r1.total_sources == r2.total_sources
    assert r1.freshness_score == r2.freshness_score
    assert [r.check for r in r1.results] == [r.check for r in r2.results]
    for c1, c2 in zip(r1.results, r2.results):
        assert len(c1.findings) == len(c2.findings), c1.check
        assert c1.stats.keys() == c2.stats.keys(), c1.check
        # severities are deterministic; titles too
        assert [f.severity for f in c1.findings] == [f.severity for f in c2.findings]
        assert [f.chunk_ids for f in c1.findings] == [f.chunk_ids for f in c2.findings]


# ============================================================================
# Fix queue — contract that users' ingestion pipelines depend on.


def test_fix_queue_empty_when_no_findings():
    import json
    from raghealth.queue import render_queue_json
    from raghealth.scanner import scan
    rep = scan(_EmptyStore(), _EmptySource())
    q = json.loads(render_queue_json(rep))
    assert q["version"] == 1
    assert q["actions"] == []
    assert q["summary"]["chunks_affected"] == 0


def test_fix_queue_ordered_critical_first():
    """Actions must be sorted critical -> warning -> info so pipelines fix
    the most damaging rot first."""
    import json
    from raghealth.queue import render_queue_json
    from raghealth.demo import build_demo_report
    q = json.loads(render_queue_json(build_demo_report()))
    priority = {"critical": 0, "warning": 1, "info": 2}
    ranks = [priority.get(a["priority"], 9) for a in q["actions"]]
    assert ranks == sorted(ranks), (
        f"queue not ordered by priority: {[a['priority'] for a in q['actions']]}")
    assert set(q["summary"].keys()) >= {"chunks_affected"}


def test_fix_queue_schema_stable():
    """Locks in the fix-queue JSON contract that ingestion scripts consume."""
    import json
    from raghealth.queue import render_queue_json
    from raghealth.demo import build_demo_report
    q = json.loads(render_queue_json(build_demo_report()))
    assert set(q.keys()) >= {"version", "generated_at", "store",
                              "actions", "summary"}
    for a in q["actions"]:
        assert set(a.keys()) >= {"action", "reason", "priority"}
        assert a["action"] in {"reembed", "delete", "ingest", "review_conflict"}
        assert a["priority"] in {"critical", "warning", "info"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"[ok] {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
