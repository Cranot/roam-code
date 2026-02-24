"""Identify affected packages/modules from a git diff via transitive dependency walk.

Given changed files, walks forward through the dependency graph to find all
transitively affected files, grouping them by impact depth (DIRECT, TRANSITIVE-1,
TRANSITIVE-2+).  Also identifies affected test files and entry points that may
need integration testing.
"""

from __future__ import annotations

import os
from collections import defaultdict, deque

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import (
    get_changed_files,
    resolve_changed_to_db,
    is_test_file,
)


# ---------------------------------------------------------------------------
# BFS forward walk with depth tracking
# ---------------------------------------------------------------------------

def _bfs_forward_with_depth(conn, start_sym_ids, max_depth=None):
    """Walk forward edges (dependents/reverse callers) via BFS.

    For each symbol in *start_sym_ids*, find all symbols that depend on it
    (i.e. symbols where the start symbol is a target in the edges table).

    Returns ``{symbol_id: (hop_count, via_file)}`` for every reachable
    dependent.  *via_file* is the file path of the first-hop symbol that
    led to this dependent.
    """
    # Build file lookup for via labels
    file_lookup = {}
    for row in conn.execute(
        "SELECT s.id, f.path FROM symbols s JOIN files f ON s.file_id = f.id"
    ).fetchall():
        file_lookup[row["id"]] = row["path"]

    visited = {}  # symbol_id -> (hops, via_file)
    queue = deque()  # (symbol_id, hops, via_file)

    for sid in start_sym_ids:
        visited[sid] = (0, None)
        queue.append((sid, 0, None))

    while queue:
        current_id, hops, via = queue.popleft()
        if max_depth is not None and hops >= max_depth:
            continue

        # Find all symbols that reference/call current_id
        # (edges where current_id is the target => source_id depends on it)
        dependents = conn.execute(
            "SELECT e.source_id FROM edges e WHERE e.target_id = ?",
            (current_id,),
        ).fetchall()

        for row in dependents:
            dep_id = row["source_id"]
            new_hops = hops + 1
            new_via = via if via else file_lookup.get(dep_id)

            if dep_id not in visited or visited[dep_id][0] > new_hops:
                visited[dep_id] = (new_hops, new_via)
                queue.append((dep_id, new_hops, new_via))

    return visited


# ---------------------------------------------------------------------------
# Entry point detection (lightweight — reuses graph_metrics)
# ---------------------------------------------------------------------------

def _find_affected_entry_points(conn, affected_sym_ids):
    """Find entry points among the affected symbols.

    Entry points are symbols with in_degree=0 in the graph_metrics table,
    or symbols with known entry-point decorators.
    """
    if not affected_sym_ids:
        return []

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.qualified_name, s.kind, "
        "       f.path AS file_path, s.line_start, "
        "       gm.in_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.id IN ({ph})",
        list(affected_sym_ids),
    )

    entry_points = []
    for r in rows:
        in_deg = r["in_degree"] if r["in_degree"] is not None else 0
        if in_deg == 0 and r["kind"] in ("function", "method", "class"):
            entry_points.append({
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "file": r["file_path"],
                "line": r["line_start"],
            })

    # Sort by file then name
    entry_points.sort(key=lambda e: (e["file"], e["name"]))
    return entry_points


# ---------------------------------------------------------------------------
# Module grouping
# ---------------------------------------------------------------------------

def _group_by_module(changed_files, affected_files):
    """Group files into modules (top-level directory or root).

    Returns ``{module: {"changed": count, "affected": count}}``.
    """
    modules = defaultdict(lambda: {"changed": 0, "affected": 0})

    def _module_name(path):
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 1:
            return parts[0] + "/"
        return "(root)"

    for f in changed_files:
        modules[_module_name(f)]["changed"] += 1

    for f in affected_files:
        if f not in changed_files:
            modules[_module_name(f)]["affected"] += 1

    return dict(sorted(modules.items()))


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("affected")
@click.option("--base", "base_ref", default="HEAD~1",
              help="Git ref to diff against (default: HEAD~1)")
@click.option("--depth", "max_depth", default=None, type=int,
              help="Maximum dependency depth to trace (default: unlimited)")
@click.option("--changed", "use_changed", is_flag=True,
              help="Use git diff to detect changed files (default: working tree)")
@click.pass_context
def affected(ctx, base_ref, max_depth, use_changed):
    """Identify affected files/modules from a git diff via dependency graph.

    Walks forward through the dependency graph from changed files to find
    all transitively affected code.  Groups results by impact depth
    (DIRECT, TRANSITIVE-1, TRANSITIVE-2+) and identifies affected tests
    and entry points.

    Use --base to specify the git ref to diff against (default: HEAD~1).
    Use --depth to limit the maximum dependency traversal depth.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    root = find_project_root()

    # Determine changed files
    if use_changed:
        changed = get_changed_files(root)
    else:
        changed = get_changed_files(root, commit_range=f"{base_ref}..HEAD")

    if not changed:
        if json_mode:
            click.echo(to_json(json_envelope("affected",
                summary={
                    "verdict": "No changes detected",
                    "total_affected": 0,
                    "changed_files": 0,
                },
                changed_files=[],
                affected_direct=[],
                affected_transitive_1=[],
                affected_transitive_2plus=[],
                affected_tests=[],
                affected_entry_points=[],
                by_module={},
            )))
            return
        click.echo("No changes detected.")
        return

    with open_db(readonly=True) as conn:
        # Map changed files to DB file IDs
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            if json_mode:
                click.echo(to_json(json_envelope("affected",
                    summary={
                        "verdict": "Changed files not in index",
                        "total_affected": 0,
                        "changed_files": len(changed),
                    },
                    changed_files=changed,
                    affected_direct=[],
                    affected_transitive_1=[],
                    affected_transitive_2plus=[],
                    affected_tests=[],
                    affected_entry_points=[],
                    by_module={},
                )))
                return
            click.echo(
                f"Changed files not found in index ({len(changed)} files).\n"
                "Try running `roam index` first."
            )
            return

        changed_paths = set(file_map.keys())

        # Collect all symbol IDs in changed files
        start_sym_ids = set()
        for path, fid in file_map.items():
            syms = conn.execute(
                "SELECT id FROM symbols WHERE file_id = ?", (fid,)
            ).fetchall()
            start_sym_ids.update(s["id"] for s in syms)

        # BFS forward to find all dependents
        reachable = _bfs_forward_with_depth(conn, start_sym_ids, max_depth)

        # Resolve all reachable symbols to file paths
        reachable_ids = [
            sid for sid in reachable if sid not in start_sym_ids
        ]

        sym_to_file = {}
        if reachable_ids:
            rows = batched_in(
                conn,
                "SELECT s.id, s.name, s.kind, f.path AS file_path "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.id IN ({ph})",
                reachable_ids,
            )
            for r in rows:
                sym_to_file[r["id"]] = r["file_path"]

        # Classify affected files by depth
        affected_direct = set()  # files that were changed (depth 0)
        affected_t1 = {}  # file -> via (1 hop)
        affected_t2plus = {}  # file -> via (2+ hops)
        all_affected_sym_ids = set()

        for sid, (hops, via) in reachable.items():
            if sid in start_sym_ids:
                continue  # skip the seed symbols
            fpath = sym_to_file.get(sid)
            if not fpath or fpath in changed_paths:
                continue  # skip if in changed files or unknown

            all_affected_sym_ids.add(sid)

            if hops == 1:
                if fpath not in affected_t1:
                    affected_t1[fpath] = via or "?"
            else:
                if fpath not in affected_t1 and fpath not in affected_t2plus:
                    affected_t2plus[fpath] = via or "?"

        # Identify test files among affected
        affected_test_files = sorted(
            f for f in (set(affected_t1) | set(affected_t2plus))
            if is_test_file(f)
        )

        # Also find colocated test files for changed files
        colocated_tests = _find_colocated_test_files(conn, changed_paths)
        for tf in colocated_tests:
            if tf not in affected_test_files and tf not in changed_paths:
                affected_test_files.append(tf)
        affected_test_files.sort()

        # Find affected entry points
        entry_points = _find_affected_entry_points(conn, all_affected_sym_ids)

        # Module grouping
        all_affected_files = set(affected_t1) | set(affected_t2plus)
        by_module = _group_by_module(changed_paths, all_affected_files)

        # Build totals
        total_affected = len(changed_paths) + len(affected_t1) + len(affected_t2plus)
        n_direct = len(changed_paths)
        n_t1 = len(affected_t1)
        n_t2 = len(affected_t2plus)

        verdict = (
            f"{total_affected} files affected by {n_direct} changes "
            f"({n_direct} direct, {n_t1} transitive-1, {n_t2} transitive-2+)"
        )

        # Build structured lists for output
        direct_list = sorted(changed_paths)
        t1_list = [
            {"file": f, "reason": f"imports {v}"}
            for f, v in sorted(affected_t1.items())
        ]
        t2_list = [
            {"file": f, "reason": f"via {v}"}
            for f, v in sorted(affected_t2plus.items())
        ]
        ep_list = [
            {
                "name": e["name"],
                "kind": e["kind"],
                "file": e["file"],
                "line": e["line"],
            }
            for e in entry_points
        ]

        # ----- JSON output -----
        if json_mode:
            click.echo(to_json(json_envelope("affected",
                summary={
                    "verdict": verdict,
                    "total_affected": total_affected,
                    "changed_files": n_direct,
                    "transitive_1": n_t1,
                    "transitive_2plus": n_t2,
                    "affected_tests": len(affected_test_files),
                    "affected_entry_points": len(entry_points),
                },
                budget=token_budget,
                changed_files=direct_list,
                affected_direct=direct_list,
                affected_transitive_1=t1_list,
                affected_transitive_2plus=t2_list,
                affected_tests=affected_test_files,
                affected_entry_points=ep_list,
                by_module=by_module,
            )))
            return

        # ----- Text output -----
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Changed (direct)
        if direct_list:
            click.echo(f"CHANGED (direct):")
            for f in direct_list:
                click.echo(f"  {f}")
            click.echo()

        # Affected (1 hop)
        if t1_list:
            click.echo(f"AFFECTED (1 hop):")
            for item in t1_list:
                click.echo(f"  {item['file']} -- {item['reason']}")
            click.echo()

        # Affected (2+ hops)
        if t2_list:
            click.echo(f"AFFECTED (2+ hops):")
            for item in t2_list:
                click.echo(f"  {item['file']} -- {item['reason']}")
            click.echo()

        # Affected tests
        if affected_test_files:
            click.echo(f"AFFECTED TESTS: {len(affected_test_files)} test files")
            for f in affected_test_files:
                click.echo(f"  {f}")
            click.echo()

        # Entry points
        if entry_points:
            click.echo(f"AFFECTED ENTRY POINTS: {len(entry_points)}")
            for e in entry_points:
                click.echo(
                    f"  {abbrev_kind(e['kind'])} {e['name']} "
                    f"at {loc(e['file'], e['line'])}"
                )
            click.echo()

        # By module
        if by_module:
            click.echo("BY MODULE:")
            for mod, counts in by_module.items():
                click.echo(
                    f"  {mod}: {counts['changed']} changed, "
                    f"{counts['affected']} affected"
                )


# ---------------------------------------------------------------------------
# Colocated test detection (lightweight)
# ---------------------------------------------------------------------------

def _find_colocated_test_files(conn, source_paths):
    """Find test files in the same directories as the given source files."""
    dirs = set()
    for fp in source_paths:
        d = os.path.dirname(fp.replace("\\", "/"))
        if d:
            dirs.add(d)

    colocated = []
    for d in dirs:
        pattern = f"{d}/%"
        rows = conn.execute(
            "SELECT path FROM files WHERE path LIKE ?", (pattern,)
        ).fetchall()
        for r in rows:
            p = r["path"]
            if is_test_file(p) and p not in source_paths:
                colocated.append(p)

    return sorted(set(colocated))
