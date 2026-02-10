"""Show blast radius of uncommitted changes."""

import subprocess

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json
from roam.commands.resolve import ensure_index


def _get_changed_files(root, staged, commit_range=None):
    """Get list of changed files from git diff."""
    cmd = ["git", "diff", "--name-only"]
    if commit_range:
        cmd.append(commit_range)
    elif staged:
        cmd.append("--cached")
    try:
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=10, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return []
        return [
            p.replace("\\", "/")
            for p in result.stdout.strip().splitlines()
            if p.strip()
        ]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


@click.command("diff")
@click.argument('commit_range', required=False, default=None)
@click.option('--staged', is_flag=True, help='Analyze staged changes instead of unstaged')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.pass_context
def diff_cmd(ctx, commit_range, staged, full):
    """Show blast radius: what code is affected by your changes.

    Optionally pass a COMMIT_RANGE (e.g. HEAD~3..HEAD, abc123, main..feature)
    to analyze committed changes instead of uncommitted ones.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    changed = _get_changed_files(root, staged, commit_range)
    if not changed:
        if commit_range:
            label = commit_range
        else:
            label = "staged" if staged else "unstaged"
        click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        # Map changed files to file IDs
        file_map = {}
        for path in changed:
            row = conn.execute(
                "SELECT id, path FROM files WHERE path = ?", (path,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{path}",),
                ).fetchone()
            if row:
                file_map[row["path"]] = row["id"]

        if not file_map:
            click.echo(f"Changed files not found in index ({len(changed)} files changed).")
            click.echo("Try running `roam index` first.")
            return

        # Get symbols in changed files
        sym_by_file = {}
        for path, fid in file_map.items():
            syms = conn.execute(
                "SELECT id, name, kind FROM symbols WHERE file_id = ?", (fid,)
            ).fetchall()
            sym_by_file[path] = syms

        total_syms = sum(len(s) for s in sym_by_file.values())

        # Build graph and compute impact
        try:
            from roam.graph.builder import build_symbol_graph
            import networkx as nx
        except ImportError:
            click.echo("Graph module not available.")
            return

        G = build_symbol_graph(conn)
        RG = G.reverse()

        # Per-file impact analysis
        file_impacts = []
        all_affected_files = set()
        all_affected_syms = set()

        for path, syms in sym_by_file.items():
            file_dependents = set()
            file_affected_files = set()
            for s in syms:
                sid = s["id"]
                if sid in RG:
                    deps = nx.descendants(RG, sid)
                    file_dependents.update(deps)
                    for d in deps:
                        node = G.nodes.get(d, {})
                        fp = node.get("file_path")
                        if fp and fp != path:
                            file_affected_files.add(fp)

            all_affected_syms.update(file_dependents)
            all_affected_files.update(file_affected_files)

            file_impacts.append({
                "path": path,
                "symbols": len(syms),
                "affected_syms": len(file_dependents),
                "affected_files": len(file_affected_files),
            })

        # Sort by blast radius
        file_impacts.sort(key=lambda x: x["affected_syms"], reverse=True)

        if json_mode:
            click.echo(to_json({
                "label": commit_range or ("staged" if staged else "unstaged"),
                "changed_files": len(file_map),
                "symbols_defined": total_syms,
                "affected_symbols": len(all_affected_syms),
                "affected_files": len(all_affected_files),
                "per_file": file_impacts,
                "blast_radius": sorted(all_affected_files),
            }))
            return

        # Output
        if commit_range:
            label = commit_range
        else:
            label = "staged" if staged else "unstaged"
        click.echo(f"=== Blast Radius ({label} changes) ===\n")
        click.echo(f"Changed files: {len(file_map)}  Symbols defined: {total_syms}")
        click.echo(f"Affected symbols: {len(all_affected_syms)}  Affected files: {len(all_affected_files)}")
        click.echo()

        # Per-file breakdown
        rows = []
        display = file_impacts if full else file_impacts[:15]
        for fi in display:
            rows.append([
                fi["path"],
                str(fi["symbols"]),
                str(fi["affected_syms"]),
                str(fi["affected_files"]),
            ])
        click.echo(format_table(
            ["Changed file", "Symbols", "Affected syms", "Affected files"],
            rows,
        ))
        if not full and len(file_impacts) > 15:
            click.echo(f"\n(+{len(file_impacts) - 15} more files)")

        # List affected files
        if all_affected_files:
            click.echo(f"\nFiles in blast radius ({len(all_affected_files)}):")
            sorted_files = sorted(all_affected_files)
            show = sorted_files if full else sorted_files[:20]
            for fp in show:
                click.echo(f"  {fp}")
            if not full and len(sorted_files) > 20:
                click.echo(f"  (+{len(sorted_files) - 20} more)")
