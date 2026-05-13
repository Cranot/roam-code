"""Show cross-language symbol bridges detected in the project."""

from __future__ import annotations

import click

from roam.capability import roam_capability
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


def _emit_xlang_envelope(
    verdict: str,
    bridge_summaries,
    all_links,
    *,
    warning: str | None = None,
    state: str | None = None,
    partial_success: bool = False,
    recommended_scope: str | None = None,
    bridge_files: int | None = None,
) -> None:
    """JSON envelope output for x-lang."""
    summary = {
        "verdict": verdict,
        "bridges": len(bridge_summaries),
        "links": len(all_links),
    }
    if warning is not None:
        summary["warning"] = warning
    if state is not None:
        summary["state"] = state
    if partial_success:
        summary["partial_success"] = True
    if recommended_scope is not None:
        summary["recommended_scope"] = recommended_scope
    if bridge_files is not None:
        summary["bridge_files"] = bridge_files
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


def _suggest_scope(file_paths: list[str]) -> str:
    """Suggest a reasonable --scope prefix from the indexed file paths.

    Picks the most populous top-level directory, falling back to ``"src/"``.
    """
    from collections import Counter

    top: Counter = Counter()
    for p in file_paths:
        norm = p.replace("\\", "/")
        head = norm.split("/", 1)[0] if "/" in norm else norm
        if head and not head.startswith("."):
            top[head + "/"] += 1
    if not top:
        return "src/"
    return top.most_common(1)[0][0]


@roam_capability(
    name="x-lang",
    category="architecture",
    summary="Show cross-language symbol bridges detected in the project",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("x-lang")
@click.option(
    "--scope",
    default=None,
    type=str,
    help="Restrict analysis to files whose path starts with this prefix (e.g. 'src/').",
)
@click.pass_context
def xlang(ctx, scope):
    """Show cross-language symbol bridges detected in the project.

    Detects and reports cross-language boundaries such as:
    - Protobuf .proto -> generated Go/Java/Python stubs
    - Salesforce Apex -> Aura/LWC/Visualforce templates

    Shows cross-language symbol links discovered by active bridges (Protobuf,
    Salesforce Apex, REST API, template, config). Use ``bridges`` to see which
    bridge types are available.  On large repos, pass ``--scope <prefix>`` to
    restrict analysis to a subtree (e.g. ``--scope src/``).
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

        # Apply --scope filter to the file list before bridge detection.
        if scope:
            scope_norm = scope.replace("\\", "/").lstrip("./")
            file_paths = [
                p for p in file_paths if p.replace("\\", "/").startswith(scope_norm)
            ]
            if not file_paths:
                verdict = f"no files under scope '{scope}'"
                if json_mode:
                    _emit_xlang_envelope(
                        verdict, [], [], state="empty_scope", partial_success=True
                    )
                else:
                    click.echo(f"VERDICT: {verdict}\n")
                    click.echo(f"No files matched scope prefix '{scope}'.")
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
        # When the scan would be too wide, emit a `state: "consider_scope"`
        # envelope naming the recommended scope rather than running unbounded.
        total_bridge_files = sum(_bridge_files_count(b, file_paths) for b in active)
        if total_bridge_files > _MAX_BRIDGE_FILES and not scope:
            recommended = _suggest_scope(file_paths)
            verdict = (
                f"Run roam x-lang --scope {recommended} to analyze cross-language "
                f"bridges (graph has {total_bridge_files} bridge files, threshold {_MAX_BRIDGE_FILES})"
            )
            if json_mode:
                _emit_xlang_envelope(
                    verdict,
                    [],
                    [],
                    state="consider_scope",
                    partial_success=True,
                    recommended_scope=recommended,
                    bridge_files=total_bridge_files,
                )
            else:
                click.echo(f"VERDICT: {verdict}")
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
