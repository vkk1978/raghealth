"""raghealth CLI.

    raghealth scan --config raghealth.yaml [--html report.html] [--json report.json]
    raghealth demo [--html demo_report.html]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _write_outputs(report, args) -> None:
    from .report import render_html, render_json, render_terminal
    render_terminal(report)
    if args.html:
        Path(args.html).write_text(render_html(report))
        print(f"\nHTML report written to {args.html}")
    if args.json:
        Path(args.json).write_text(render_json(report))
        print(f"JSON report written to {args.json}")
    if getattr(args, "fix_queue", None):
        from .queue import render_queue_json
        Path(args.fix_queue).write_text(render_queue_json(report))
        print(f"fix queue written to {args.fix_queue}")


def _build_canary_parts(cfg, canaries_path):
    from .canary import CanarySet
    from .embedders import build_embedder
    if not cfg.get("embedder"):
        raise SystemExit("canaries need an 'embedder:' section in raghealth.yaml "
                         "(the same model your pipeline uses)")
    return CanarySet.load(canaries_path), build_embedder(cfg["embedder"])


def cmd_scan(args) -> int:
    from .config import build_source, build_store, load_config, parse_scan_options
    from .scanner import scan

    cfg = load_config(args.config)
    store = build_store(dict(cfg["store"]))
    source = build_source(dict(cfg["source"]))
    try:
        opts = parse_scan_options(cfg)
        if args.redact:
            opts["redact"] = True
        if getattr(args, "canaries", None):
            opts["canary_set"], opts["embedder"] = _build_canary_parts(cfg, args.canaries)
        report = scan(store, source, **opts)
    finally:
        store.close()
        source.close()
    _write_outputs(report, args)
    # exit code 1 if any critical findings — usable in CI
    crit = sum(r.critical_count for r in report.results)
    return 1 if (crit and args.fail_on_critical) else 0


def cmd_init(args) -> int:
    from .init_wizard import run_init
    return run_init(args)


def _canary_setup(args):
    from .config import build_store, load_config
    cfg = load_config(args.config)
    cset, embedder = _build_canary_parts(cfg, args.canaries)
    return build_store(dict(cfg["store"])), cset, embedder


def cmd_canary_baseline(args) -> int:
    from .canary import baseline_payload, run_canaries, save_json
    store, cset, embedder = _canary_setup(args)
    try:
        results = run_canaries(store, embedder, cset)
    finally:
        store.close()
    save_json(baseline_payload(cset, results), args.out)
    print(f"baseline captured: {len(results)} canaries x top-{cset.k} -> {args.out}")
    return 0


def cmd_canary_check(args) -> int:
    from rich.console import Console
    from .canary import check_against_baseline, load_json, run_canaries
    store, cset, embedder = _canary_setup(args)
    try:
        results = run_canaries(store, embedder, cset)
    finally:
        store.close()
    drifts = check_against_baseline(load_json(args.baseline), results)
    console = Console()
    mean = sum(d.overlap_pct for d in drifts) / len(drifts) if drifts else 100.0
    for d in drifts:
        color = "green" if d.overlap_pct >= 85 else ("yellow" if d.overlap_pct >= 70 else "red")
        console.print(f"[{color}]{d.overlap_pct:5.1f}%[/] {d.canary_id}: {d.query}")
        if d.missing:
            console.print(f"        [red]gone from top-k:[/] {', '.join(d.missing)}")
        if d.new:
            console.print(f"        [yellow]new in top-k:[/] {', '.join(d.new)}")
    console.print(f"\nmean overlap vs baseline: [bold]{mean:.1f}%[/] "
                  "(healthy systems hold 85-95%)")
    if args.fail_under is not None and mean < args.fail_under:
        console.print(f"[red]FAIL[/] below --fail-under {args.fail_under}")
        return 1
    return 0


def cmd_agent(args) -> int:
    from .agent import run_agent
    return run_agent(args)


def cmd_diff(args) -> int:
    from .diffing import diff_reports, load_report, render_diff_terminal
    d = diff_reports(load_report(args.old), load_report(args.new))
    render_diff_terminal(d, args.old, args.new)
    return 1 if (args.fail_on_regression and d.regressed) else 0


def cmd_demo(args) -> int:
    from .demo import build_demo_report
    report = build_demo_report()
    _write_outputs(report, args)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="raghealth",
                                description="Health checks for RAG knowledge bases")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan a vector store against its source of truth")
    s.add_argument("--config", "-c", default="raghealth.yaml")
    s.add_argument("--html", help="write HTML report to this path")
    s.add_argument("--json", help="write JSON report to this path")
    s.add_argument("--fail-on-critical", action="store_true",
                   help="exit 1 if critical findings exist (for CI)")
    s.add_argument("--redact", action="store_true",
                   help="strip chunk content from all output (metadata-only report)")
    s.add_argument("--fix-queue", metavar="PATH",
                   help="write an actionable reembed/delete/ingest job (JSON)")
    s.add_argument("--canaries", metavar="PATH",
                   help="canaries.yaml — enables blast-radius scoring")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("canary", help="baseline and drift-check canonical queries")
    csub = c.add_subparsers(dest="canary_cmd", required=True)
    cb = csub.add_parser("baseline", help="capture top-k results for each canary")
    cb.add_argument("--config", "-c", default="raghealth.yaml")
    cb.add_argument("--canaries", default="canaries.yaml")
    cb.add_argument("--out", "-o", default="canary_baseline.json")
    cb.set_defaults(func=cmd_canary_baseline)
    cc = csub.add_parser("check", help="compare current results to the baseline")
    cc.add_argument("--config", "-c", default="raghealth.yaml")
    cc.add_argument("--canaries", default="canaries.yaml")
    cc.add_argument("--baseline", "-b", default="canary_baseline.json")
    cc.add_argument("--fail-under", type=float, metavar="PCT",
                    help="exit 1 if mean overlap drops below PCT (e.g. 70)")
    cc.set_defaults(func=cmd_canary_check)

    f = sub.add_parser("diff", help="compare two scan JSON reports")
    f.add_argument("old", help="previous scan (raghealth scan --json ...)")
    f.add_argument("new", help="latest scan")
    f.add_argument("--fail-on-regression", action="store_true",
                   help="exit 1 if new findings appeared or the score dropped")
    f.set_defaults(func=cmd_diff)

    i = sub.add_parser("init", help="interactive setup: introspect DB, write raghealth.yaml")
    i.add_argument("--store", choices=["pgvector", "chroma"])
    i.add_argument("--dsn", help="Postgres DSN (pgvector)")
    i.add_argument("--chroma-path", help="Chroma persistent dir")
    i.add_argument("--source", choices=["filesystem", "notion"])
    i.add_argument("--source-root", help="docs root (filesystem source)")
    i.add_argument("--config", "-c", default="raghealth.yaml", help="output path")
    i.add_argument("--yes", "-y", action="store_true", help="accept all guesses")
    i.set_defaults(func=cmd_init)

    a = sub.add_parser("agent", help="scan on a schedule and push findings to a raghealth server")
    a.add_argument("--config", "-c", default="raghealth.yaml")
    a.add_argument("--once", action="store_true", help="single scan+push (cron mode)")
    a.add_argument("--interval", help="e.g. 6h, 30m, 1d (daemon mode)")
    a.add_argument("--url", help="override push.url")
    a.add_argument("--api-key", help="override push.api_key")
    a.add_argument("--kb", help="override push.kb")
    a.set_defaults(func=cmd_agent)

    d = sub.add_parser("demo", help="run against built-in synthetic data (no DB needed)")
    d.add_argument("--html", help="write HTML report to this path")
    d.add_argument("--json", help="write JSON report to this path")
    d.add_argument("--fix-queue", metavar="PATH",
                   help="write an actionable reembed/delete/ingest job (JSON)")
    d.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
