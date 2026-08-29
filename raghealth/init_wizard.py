"""`raghealth init` — interactive setup wizard.

Connects to the vector store, introspects the schema, guesses the column
mapping and metadata keys, shows its reasoning, and writes raghealth.yaml.
`--yes` accepts all guesses (non-interactive / CI / scripting).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .introspect import TableGuess, introspect_pgvector

console = Console()


def _ask(prompt: str, default: str = "", yes: bool = False) -> str:
    if yes:
        return default
    suffix = f" [dim]({default})[/dim]" if default else ""
    val = console.input(f"[bold]{prompt}[/bold]{suffix}: ").strip()
    return val or default


def _confirm(prompt: str, yes: bool = False) -> bool:
    if yes:
        return True
    return _ask(f"{prompt} (y/n)", "y").lower().startswith("y")


def _pick_table(guesses: list[TableGuess], yes: bool) -> TableGuess:
    if len(guesses) == 1:
        return guesses[0]
    t = Table(title="Tables with vector columns", show_header=True)
    t.add_column("#"); t.add_column("table"); t.add_column("rows", justify="right")
    for i, g in enumerate(guesses):
        t.add_row(str(i + 1), f"{g.schema}.{g.table}", str(g.row_count))
    console.print(t)
    # default: the biggest table
    default = str(1 + max(range(len(guesses)), key=lambda i: guesses[i].row_count))
    idx = int(_ask("Which table holds your chunks?", default, yes)) - 1
    return guesses[idx]


def _show_guess(g: TableGuess) -> None:
    t = Table(show_header=False, box=None)
    t.add_row("table", f"{g.schema}.{g.table} ({g.row_count} rows)")
    t.add_row("id column", g.id_col or "[red]not found[/red]")
    t.add_row("content column", g.content_col or "[red]not found[/red]")
    t.add_row("embedding column", g.embedding_col or "[red]not found[/red]")
    t.add_row("metadata column", g.metadata_col or "[yellow]none[/yellow]")
    src = (f"{g.metadata_col}->>'{g.source_key}'" if g.source_key
           else "[red]NOT DETECTED — staleness/orphan checks need this[/red]")
    t.add_row("source path", src)
    if g.embedded_at_col:
        ts = g.embedded_at_col
    elif g.timestamp_key:
        ts = f"{g.metadata_col}->>'{g.timestamp_key}'"
    else:
        ts = "[yellow]none — set scan.assume_embedded_at as a fallback[/yellow]"
    t.add_row("embedded at", ts)
    if g.sample_paths:
        t.add_row("sample paths", "\n".join(g.sample_paths[:3]))
    console.print(Panel(t, title="detected schema", border_style="cyan"))


def run_init(args) -> int:
    yes = bool(getattr(args, "yes", False))
    console.print("[bold]raghealth init[/bold] — let's connect your knowledge base.\n")

    store_type = getattr(args, "store", None) or _ask(
        "Vector store type (pgvector/chroma)", "pgvector", yes)

    store_cfg: dict
    if store_type == "pgvector":
        dsn = getattr(args, "dsn", None) or _ask(
            "Postgres DSN", "postgresql://user:pass@localhost:5432/postgres", yes)
        console.print("[dim]connecting (read-only) and introspecting…[/dim]")
        guesses = introspect_pgvector(dsn)
        if not guesses:
            console.print("[red]No tables with pgvector columns found in this database.[/red]")
            return 1
        g = _pick_table(guesses, yes)
        _show_guess(g)
        if not _confirm("Use this mapping?", yes):
            console.print("Edit raghealth.yaml by hand after init — writing best guess anyway.")
        store_cfg = g.to_store_config(dsn)
    elif store_type == "chroma":
        from .introspect import introspect_chroma
        path = getattr(args, "chroma_path", None) or _ask("Chroma path", "./chroma_db", yes)
        cols = introspect_chroma(path=path)
        if not cols:
            console.print("[red]No collections found.[/red]")
            return 1
        info = cols[0] if (yes or len(cols) == 1) else cols[int(_ask(
            "Collection # " + ", ".join(f"{i+1}={c['collection']}" for i, c in enumerate(cols)),
            "1")) - 1]
        console.print(f"collection [bold]{info['collection']}[/bold]: {info['count']} chunks, "
                      f"source key: {info['source_key']}, timestamp key: {info['timestamp_key']}")
        store_cfg = {"type": "chroma", "path": path, "collection": info["collection"],
                     "source_path_key": info["source_key"] or "source",
                     "embedded_at_key": info["timestamp_key"] or "embedded_at"}
    else:
        console.print(f"[red]unknown store type {store_type!r}[/red]")
        return 1

    source_type = getattr(args, "source", None) or _ask(
        "Source of truth (filesystem/notion)", "filesystem", yes)
    if source_type == "filesystem":
        root = getattr(args, "source_root", None) or _ask("Docs root directory", "./docs", yes)
        source_cfg = {"type": "filesystem", "root": root}
    else:
        source_cfg = {"type": "notion", "token": "env:NOTION_TOKEN", "path_style": "id"}
        console.print("[dim]Set NOTION_TOKEN in your environment before scanning.[/dim]")

    cfg = {"store": store_cfg, "source": source_cfg,
           "scan": {"grace_days": 7, "duplicate_threshold": 0.97,
                    "include_embeddings": True}}
    out = Path(getattr(args, "config", None) or "raghealth.yaml")
    out.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False), encoding="utf-8")
    console.print(f"\n[green]✓[/green] wrote [bold]{out}[/bold]")
    console.print("Next: [bold]raghealth scan --html report.html[/bold]")
    return 0
