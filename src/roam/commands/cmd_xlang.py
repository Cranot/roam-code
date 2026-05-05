"""Show cross-language symbol bridges detected in the project."""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

_MAX_BRIDGE_FILES = 1000


def _xlang_verdict(bridge_summaries, all_links) -> str:
    """One-line verdict shared by JSON and text output."""
    if not bridge_summaries:
        return "no cross-language bridges detected"
    bnames = ", ".join(bs["name"] for bs in bridge_summaries[:3])
    return f"{len(bridge_summaries)} bridges ({bnames}), {len(all_links)} links"


def _emit_xlang_envelope(verdict: str, bridge_summaries, all_links, *, warning: str | None = None) -> None:
    """JSON envelope output for x-lang."""
    summary = {
        "verdict": verdict,
        "bridges": len(bridge_summaries),
        "links": len(all_links),
    }
    if warning is not None:
        summary["warning"] = warning
    click.echo(
        to_json(
            json_envelope(
                "x-lang",
                summary=summary,
                bridges=bridge_summaries,
                links=all_links[:200] if all_links else [],
            )
        )
    )


def _resolve_bridge(bridge, file_paths, conn) -> tuple[list, dict | None]:
    """Resolve all cross-language links for a single bridge.

    Returns ``(links, summary)`` where ``summary`` is None when the bridge
    has no source or target files in this project (we skip emitting an
    empty row)."""
    source_files = [p for p in file_paths if any(p.endswith(ext) for ext in bridge.source_extensions)]
    target_files_paths = [p for p in file_paths if any(p.endswith(ext) for ext in bridge.target_extensions)]
    if not source_files or not target_files_paths:
        return [], None

    links: list = []
    for src_path in source_files:
        src_syms = _get_file_symbols(conn, src_path)
        if not src_syms:
            continue
        target_files: dict = {}
        for tp in target_files_paths:
            tgt_syms = _get_file_symbols(conn, tp)
            if tgt_syms:
                target_files[tp] = tgt_syms
        if not target_files:
            continue
        links.extend(bridge.resolve(src_path, src_syms, target_files))

    summary = {
        "name": bridge.name,
        "source_files": len(source_files),
        "target_files": len(target_files_paths),
        "links": len(links),
        "source_extensions": sorted(bridge.source_extensions),
        "target_extensions": sorted(bridge.target_extensions),
    }
    return links, summary


def _bridge_files_count(bridge, file_paths) -> int:
    """Total source+target file count for one bridge."""
    src = sum(1 for p in file_paths if any(p.endswith(ext) for ext in bridge.source_extensions))
    tgt = sum(1 for p in file_paths if any(p.endswith(ext) for ext in bridge.target_extensions))
    return src + tgt


def _emit_xlang_text(bridge_summaries, all_links) -> None:
    """Text output: verdict + bridges table + links sample."""
    verdict = _xlang_verdict(bridge_summaries, all_links)
    click.echo(f"VERDICT: {verdict}\n")
    click.echo(f"=== Cross-Language Bridges ({len(bridge_summaries)}) ===\n")
    if bridge_summaries:
        table_rows = [
            [
                bs["name"],
                ", ".join(bs["source_extensions"]),
                ", ".join(bs["target_extensions"]),
                str(bs["source_files"]),
                str(bs["target_files"]),
                str(bs["links"]),
            ]
            for bs in bridge_summaries
        ]
        click.echo(
            format_table(
                ["Bridge", "Source Ext", "Target Ext", "Src Files", "Tgt Files", "Links"],
                table_rows,
            )
        )
    else:
        click.echo("  (no bridges active)")
    if all_links:
        click.echo(f"\n=== Cross-Language Links ({len(all_links)}) ===\n")
        shown = min(20, len(all_links))
        for link in all_links[:shown]:
            click.echo(f"  {link.get('source', '?')} -> {link.get('target', '?')}  ({link.get('bridge', '?')})")
        if len(all_links) > shown:
            click.echo(f"\n  (+{len(all_links) - shown} more)")


@click.command("x-lang")
@click.pass_context
def xlang(ctx):
    """Show cross-language symbol bridges detected in the project.

    Detects and reports cross-language boundaries such as:
    - Protobuf .proto -> generated Go/Java/Python stubs
    - Salesforce Apex -> Aura/LWC/Visualforce templates

    Shows cross-language symbol links discovered by active bridges (Protobuf,
    Salesforce Apex, REST API, template, config). Use ``bridges`` to see which
    bridge types are available.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        rows = conn.execute("SELECT path FROM files").fetchall()
        file_paths = [r["path"] for r in rows]
        if not file_paths:
            if json_mode:
                _emit_xlang_envelope("no files indexed", [], [])
            else:
                click.echo("VERDICT: no files indexed\n")
                click.echo("No files indexed.")
            return

        from roam.bridges.registry import detect_bridges

        active = detect_bridges(file_paths)
        if not active:
            verdict = "no cross-language bridges detected"
            if json_mode:
                _emit_xlang_envelope(verdict, [], [])
            else:
                click.echo(f"VERDICT: {verdict}\n")
                click.echo("No cross-language bridges detected.")
            return

        # Performance guard: cap total source+target file count.
        total_bridge_files = sum(_bridge_files_count(b, file_paths) for b in active)
        if total_bridge_files > _MAX_BRIDGE_FILES:
            warning = (
                f"Too many bridge files ({total_bridge_files} source+target) for cross-language analysis. "
                "Index a subdirectory to reduce scope."
            )
            if json_mode:
                _emit_xlang_envelope(f"skipped: {warning}", [], [], warning=warning)
            else:
                click.echo(f"WARNING: {warning}")
            return

        all_links: list = []
        bridge_summaries: list = []
        for bridge in active:
            links, summary = _resolve_bridge(bridge, file_paths, conn)
            if summary is None:
                continue
            all_links.extend(links)
            bridge_summaries.append(summary)

        if json_mode:
            _emit_xlang_envelope(_xlang_verdict(bridge_summaries, all_links), bridge_summaries, all_links)
            return
        _emit_xlang_text(bridge_summaries, all_links)


def _get_file_symbols(conn, path):
    """Get symbols for a file path from the DB."""
    frow = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    if not frow:
        return []
    syms = conn.execute(
        "SELECT name, qualified_name, kind FROM symbols WHERE file_id = ?",
        (frow["id"],),
    ).fetchall()
    return [{"name": s["name"], "qualified_name": s["qualified_name"], "kind": s["kind"]} for s in syms]
