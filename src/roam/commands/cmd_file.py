"""Show file skeleton: all definitions with signatures."""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files
from roam.commands.resolve import ensure_index, file_not_found_hint
from roam.db.connection import find_project_root, open_db
from roam.db.queries import FILE_BY_PATH, SYMBOLS_IN_FILE
from roam.output.formatter import abbrev_kind, format_signature, json_envelope, to_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_file(conn, path):
    """Resolve a file path to its DB row, or None."""
    path = path.replace("\\", "/")
    frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{path}",),
        ).fetchone()
    return frow


def _get_changed_files():
    """Get list of uncommitted changed file paths from git (staged + unstaged)."""
    root = find_project_root()
    unstaged = get_changed_files(root)
    staged = get_changed_files(root, staged=True)
    return sorted(set(unstaged) | set(staged))


def _get_deps_of_file(conn, frow):
    """Get file paths that the given file imports (outgoing file edges)."""
    rows = conn.execute(
        "SELECT f.path FROM file_edges fe "
        "JOIN files f ON fe.target_file_id = f.id "
        "WHERE fe.source_file_id = ? "
        "ORDER BY f.path",
        (frow["id"],),
    ).fetchall()
    return [r["path"] for r in rows]


def _build_file_skeleton(conn, frow):
    """Build the skeleton data for a single file row.

    Returns (frow, symbols, kind_counts, parent_ids).
    Enriches frow with file_stats data (cognitive_load, health_score).
    """
    symbols = conn.execute(SYMBOLS_IN_FILE, (frow["id"],)).fetchall()
    kind_counts = Counter(abbrev_kind(s["kind"]) for s in symbols)
    parent_ids = {s["id"]: s["parent_id"] for s in symbols}

    # Enrich frow with file_stats
    stats = conn.execute(
        "SELECT cognitive_load, health_score FROM file_stats WHERE file_id = ?",
        (frow["id"],),
    ).fetchone()
    # Build a mutable dict from the sqlite3.Row
    enriched = dict(frow)
    if stats:
        enriched["cognitive_load"] = stats["cognitive_load"]
        enriched["health_score"] = stats["health_score"]
    else:
        enriched["cognitive_load"] = None
        enriched["health_score"] = None

    return enriched, symbols, kind_counts, parent_ids


def _skeleton_to_json(frow, symbols, kind_counts, parent_ids):
    """Convert a single file skeleton to a JSON-serializable dict."""

    def _depth(s):
        level = 0
        pid = s["parent_id"]
        while pid is not None and pid in parent_ids:
            level += 1
            pid = parent_ids[pid]
        return level

    return {
        "path": frow["path"],
        "language": frow["language"],
        "line_count": frow["line_count"],
        "symbol_count": len(symbols),
        "kind_summary": dict(kind_counts.most_common()),
        "cognitive_load": frow.get("cognitive_load"),
        "health_score": frow.get("health_score"),
        "symbols": [
            {
                "name": s["name"],
                "kind": s["kind"],
                "signature": s["signature"] or "",
                "line_start": s["line_start"],
                "line_end": s["line_end"],
                "depth": _depth(s),
            }
            for s in symbols
        ],
    }


def _render_skeleton_text(frow, symbols, kind_counts, parent_ids, header=None):
    """Render a single file skeleton as text lines.

    If *header* is provided, use it instead of the default file header.
    """
    lines = []

    if header:
        lines.append(header)
    else:
        meta = f"{frow['language'] or '?'}, {frow['line_count']} lines"
        cl = frow.get("cognitive_load")
        hs = frow.get("health_score")
        if cl is not None:
            meta += f", load={cl:.0f}/100"
        if hs is not None:
            meta += f", health={hs}/10"
        lines.append(f"{frow['path']}  ({meta})")
    lines.append("")

    if not symbols:
        lines.append("  (no symbols)")
        return lines

    summary_parts = [f"{k}:{v}" for k, v in kind_counts.most_common()]
    lines.append("  ".join(summary_parts))
    lines.append("")

    for s in symbols:
        level = 0
        if s["parent_id"] is not None:
            level = 1
            pid = s["parent_id"]
            while pid in parent_ids and parent_ids[pid] is not None:
                level += 1
                pid = parent_ids[pid]

        prefix = "  " * level
        kind = abbrev_kind(s["kind"])
        sig = format_signature(s["signature"])
        line_info = f"L{s['line_start']}"
        if s["line_end"] and s["line_end"] != s["line_start"]:
            line_info += f"-{s['line_end']}"

        parts = [kind, s["name"]]
        if sig:
            parts.append(sig)
        parts.append(line_info)
        lines.append(f"{prefix}{'  '.join(parts)}")

    return lines


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="file",
    category="exploration",
    summary="Show file skeleton: all definitions with signatures",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("file")
@click.argument("paths", nargs=-1)
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.option(
    "--changed",
    is_flag=True,
    default=False,
    help="Show skeletons of all uncommitted changed files",
)
@click.option(
    "--deps-of",
    "deps_of",
    type=str,
    default=None,
    help="Show skeleton of PATH plus all files it imports",
)
@click.pass_context
def file_cmd(ctx, paths, full, changed, deps_of):
    """Show file skeleton: all definitions with signatures.

    Unlike ``context`` (which provides change-oriented analysis for a symbol),
    this command shows the structural skeleton of a file: all definitions with
    signatures and line ranges.

    Accepts one or more file paths.  With --changed, shows skeletons of all
    uncommitted changed files.  With --deps-of PATH, shows the skeleton of
    PATH plus every file it imports.

    \b
    Examples:
      roam file src/auth.py
      roam file src/auth.py src/session.py --full
      roam file --changed
      roam file --deps-of src/auth.py

    See also ``context`` (files + line ranges for a symbol),
    ``deps`` (raw imports/imported-by), and ``module`` (package-level
    skeleton).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # Collect target paths from all sources
    target_paths = list(paths)

    if changed:
        target_paths.extend(_get_changed_files())

    if deps_of:
        # Always include the deps-of file itself, plus its imports
        target_paths.append(deps_of)

    # If nothing to do, print help
    if not target_paths and not deps_of:
        click.echo(ctx.get_help())
        return

    with open_db(readonly=True) as conn:
        # Resolve --deps-of imports
        if deps_of:
            dep_path = deps_of.replace("\\", "/")
            dep_frow = _resolve_file(conn, dep_path)
            if dep_frow is not None:
                dep_paths = _get_deps_of_file(conn, dep_frow)
                target_paths.extend(dep_paths)

        # Deduplicate while preserving order
        seen = set()
        unique_paths = []
        for p in target_paths:
            norm = p.replace("\\", "/")
            if norm not in seen:
                seen.add(norm)
                unique_paths.append(p)

        # --- Single-file mode (backward compat) ---
        if len(unique_paths) == 1:
            frow = _resolve_file(conn, unique_paths[0])
            if frow is None:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "file",
                                summary={
                                    "verdict": f"file not found: '{unique_paths[0]}'",
                                    "error": "file_not_found",
                                },
                                file=unique_paths[0],
                                hint=file_not_found_hint(unique_paths[0]),
                            )
                        )
                    )
                    raise SystemExit(1)
                click.echo(file_not_found_hint(unique_paths[0]))
                raise SystemExit(1)

            frow, symbols, kind_counts, parent_ids = _build_file_skeleton(conn, frow)

            if json_mode:
                obj = _skeleton_to_json(frow, symbols, kind_counts, parent_ids)
                _fname = frow["path"].split("/")[-1] if "/" in frow["path"] else frow["path"]
                _kind_str = ", ".join(f"{c} {k}" for k, c in kind_counts.most_common(3))
                _file_verdict = f"{_fname}: {len(symbols)} symbols ({_kind_str}), {frow['line_count']} LOC"
                click.echo(
                    to_json(
                        json_envelope(
                            "file",
                            summary={
                                "verdict": _file_verdict,
                                "symbols": len(symbols),
                                "line_count": frow["line_count"],
                            },
                            path=obj["path"],
                            language=obj["language"],
                            line_count=obj["line_count"],
                            kind_summary=obj["kind_summary"],
                            cognitive_load=obj.get("cognitive_load"),
                            health_score=obj.get("health_score"),
                            symbols=obj["symbols"],
                        )
                    )
                )
                return

            _fname_txt = frow["path"].split("/")[-1] if "/" in frow["path"] else frow["path"]
            _kind_str_txt = ", ".join(f"{c} {k}" for k, c in kind_counts.most_common(3))
            _file_verdict_txt = f"{_fname_txt}: {len(symbols)} symbols ({_kind_str_txt}), {frow['line_count']} LOC"
            click.echo(f"VERDICT: {_file_verdict_txt}")
            click.echo()
            text_lines = _render_skeleton_text(frow, symbols, kind_counts, parent_ids)
            click.echo("\n".join(text_lines))
            return

        # --- Multi-file mode ---
        file_results = []
        missing = []
        for p in unique_paths:
            frow = _resolve_file(conn, p)
            if frow is None:
                missing.append(p)
                continue
            frow, symbols, kind_counts, parent_ids = _build_file_skeleton(conn, frow)
            file_results.append((frow, symbols, kind_counts, parent_ids))

        if json_mode:
            files_json = []
            for frow, symbols, kind_counts, parent_ids in file_results:
                files_json.append(_skeleton_to_json(frow, symbols, kind_counts, parent_ids))
            total_symbols = sum(f["symbol_count"] for f in files_json)
            _multi_verdict = f"{len(files_json)} files, {total_symbols} total symbols" + (
                f", {len(missing)} not indexed" if missing else ""
            )
            click.echo(
                to_json(
                    json_envelope(
                        "file",
                        summary={
                            "verdict": _multi_verdict,
                            "files": len(files_json),
                            "total_symbols": total_symbols,
                            "missing": missing,
                        },
                        files=files_json,
                    )
                )
            )
            return

        # Text output
        _total_syms_txt = sum(len(s) for _, s, _, _ in file_results)
        _multi_verdict_txt = f"{len(file_results)} files, {_total_syms_txt} total symbols" + (
            f", {len(missing)} not indexed" if missing else ""
        )
        click.echo(f"VERDICT: {_multi_verdict_txt}")
        click.echo()
        if missing:
            for m in missing:
                click.echo(f"(not indexed: {m})")
            click.echo()

        first = True
        for frow, symbols, kind_counts, parent_ids in file_results:
            if not first:
                click.echo()

            header = f"--- {frow['path']} ({len(symbols)} symbols) ---"
            text_lines = _render_skeleton_text(
                frow,
                symbols,
                kind_counts,
                parent_ids,
                header=header,
            )
            click.echo("\n".join(text_lines))
            first = False
